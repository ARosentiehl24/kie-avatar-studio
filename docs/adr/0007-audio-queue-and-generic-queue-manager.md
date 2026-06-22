# 0007. Cola estructurada de audios + QueueManager genérico

Fecha: 2026-06-02 Estado: Aceptado

## Contexto

Hasta la Fase 1.5 los audios TTS se generaban en `AudiosController.generate` con
un patrón fire-and-forget (`run_worker(...)` que llamaba al gateway, poll
inline, persist al final). Eso traía 4 problemas operativos:

1. **Sin visibilidad**: el usuario no sabía si su audio se había encolado,
   estaba procesándose, o ya había fallado. La pantalla solo mostraba los
   `GeneratedAudio` completados; los demás estados eran invisibles.
2. **Sin durabilidad**: si la app se cerraba durante un polling, el job se
   perdía. El crédito en Kie se consumía pero no había forma de recuperar la URL
   del audio cuando volvía a iniciar.
3. **Sin cola**: dos generaciones simultáneas competían sin límite de
   paralelismo (más allá de lo que Textual `run_worker` permitía). El
   `max_parallel_jobs` solo aplicaba a `VideoJob`.
4. **Duplicación inminente**: el `JobRunner` y `QueueManager` para videos eran
   perfectos para audios, pero copiar y pegar para crear un `AudioQueueManager`
   violaba DRY/CR-3.7 y multiplicaba la superficie de bugs (concurrencia es
   difícil de testear bien dos veces).

Necesitábamos cola estructurada para audios (igual de durable que la de videos)
sin escribir un sistema paralelo.

## Decisión

Generalizamos el `QueueManager` a un componente **genérico por tipo de job y de
evento**, y montamos sobre él una cola separada de `AudioJob`, con su propio
runner, lifecycle y repositorio. Las dos colas comparten el mismo límite de
paralelismo global.

### Diagrama de capas final

```text
                ┌─────────────────────────────────────────────────────┐
                │                     ui/                             │
                │   AudiosScreen           HistoryScreen              │
                │     │                       │                       │
                └─────┼───────────────────────┼───────────────────────┘
                      │                       │
                ┌─────▼───────────────────────▼───────────────────────┐
                │                   app_layer/                        │
                │                                                     │
                │  AudiosController        HistoryController          │
                │      │                       │                      │
                │      ▼                       ▼                      │
                │  QueueManager[AudioJob,   ─── (read-only) ───        │
                │   AudioJobUpdated]       QueueManager[VideoJob,     │
                │      │                    JobUpdated]               │
                │      │                       │                      │
                │  AudioJobRunner          JobRunner                  │
                │  AudioJobLifecycle       VideoJobLifecycle          │
                │                                                     │
                │       └── capacity_limiter (Semaphore) ──┘          │
                └─────────────────────┬───────────────────────────────┘
                                      │
                ┌─────────────────────▼───────────────────────────────┐
                │                   domain/                           │
                │  RunnableJob (Protocol) · JobLifecycle[T_contra]    │
                │  AudioJob · VideoJob · HistoryEntry · events        │
                └─────────────────────┬───────────────────────────────┘
                                      │
                ┌─────────────────────▼───────────────────────────────┐
                │                    infra/                           │
                │   AudioJobsDB · JobsDB · AudiosDB · KieClient       │
                └─────────────────────────────────────────────────────┘
```

### Cambios concretos

#### Domain (Protocols + modelos)

- `RunnableJob` (Protocol): mínimo contrato que un job durable debe cumplir:
  `id`, `is_terminal()`, `is_resumable()`. NO incluye `status` porque cada job
  tiene su propio `StrEnum`.
- `RunnableRunner[T]`: `async def run(job: T) -> T`. Implementado por
  `JobRunner` y `AudioJobRunner`.
- `JobLifecycle[T_contra]` (contravariante): encapsula `is_cancellable`,
  `is_retryable`, `mark_cancelled`, `reset_for_retry`. Cada implementación
  persiste antes de mutar memoria (write-ahead).
- `AudioJob` + `AudioJobStatus` (StrEnum) + `AUDIO_RESUMABLE_STATUSES`
  - `AUDIO_TERMINAL_STATUSES`.
- `AudioJobRepository` (Protocol) espejo de `JobRepository`.
- Eventos: `AudioJobUpdated` (separado de `JobUpdated[T]` para que las pantallas
  se suscriban al stream correcto sin runtime type matching), `HistoryEntry`
  (vista normalizada para `HistoryScreen`).

#### App layer

- `QueueManager` ahora es `Generic[T: RunnableJob, EventT]`. Acepta:
  - `runner: RunnableRunner[T]` (qué ejecutar).
  - `event_factory: Callable[[T], EventT]` (cómo construir el evento).
  - `lifecycle: JobLifecycle[T]` (reglas cancel/retry/persist).
  - `capacity_limiter: asyncio.Semaphore | None` (opcional, para compartir el
    semáforo global entre múltiples queues).
  - `add_listener(cb) -> unsubscribe`: devuelve callable para que la UI
    desuscriba en `on_unmount`.
  - `cancel/retry`: ahora son `async` (persisten antes de mutar memoria).
    `restore_pending` recibe un `ResumableLoader[T]` callable en vez del repo
    (más DIP).
- `VideoJobLifecycle` / `AudioJobLifecycle`: implementaciones concretas.
  Encapsulan los frozensets de estados cancellable/retryable.
- `AudioJobRunner`: ejecuta el lifecycle
  `queued → validating → creating → polling → completed/failed`. Resume
  idempotente: si el job venía con `task_id` poblado (POLLING reanudado), reusa
  ese task en Kie en vez de crear uno nuevo (evita doble cobro).
- `AudiosController`: reescrito. `enqueue_generation` reemplaza `generate` (no
  espera al resultado). `wait_for_job` helper para la UI con patrón
  register-first-then-check (sin race condition). `subscribe`, `cancel`,
  `retry`, `delete_job` expuestos.
- `HistoryController`: read-only, agrega video + audio en `HistoryEntry` ya
  normalizado. `subscribe` registra en AMBOS queues y devuelve un unsubscribe
  atómico.

**Composition root (`app.py`)**

- `capacity_limiter = asyncio.Semaphore(max_parallel_jobs)` único, compartido
  entre `queue` (video) y `audio_queue`. Sin esto, dos queues con counters
  separados podrían llegar al doble del límite global.
- `on_mount`: además de restaurar video jobs, restaura audio jobs en
  QUEUED/POLLING y barre los CREATING → FAILED (estado indeterminado).
- `_rebuild_kie_client`: recrea TODA la cadena audio (runner + queue +
  audios_controller + history_controller) porque cada uno guarda referencias
  concretas a la anterior.

#### UI

- `AudiosScreen` muestra `AudioJob` en una tabla unificada con estado, panel de
  contadores en vivo
  (`🔄 N generando · ⏳ M en cola · ✓ K listos · ✖ X fallidos`), y acciones
  nuevas (`Cancelar job`, `Reintentar`). Se suscribe al stream en `on_mount`,
  desuscribe en `on_unmount`. Los eventos del queue se reciben en el mismo event
  loop pero se redispatchan vía Message Textual para evitar re-entrada.
- `HistoryScreen` nueva: tabla unificada read-only video + audio con filtros y
  refresh en vivo.

## Consecuencias

### Positivas

- **Visibilidad total**: el usuario ve cada job en cola, su progreso y su
  resultado sin abrir logs.
- **Durabilidad real**: matar y reabrir la app reanuda los jobs en POLLING (sin
  gastar créditos extra) y marca como FAILED los que quedaron en CREATING (sin
  reanudar a ciegas).
- **Reuso de concurrencia**: `QueueManager` único, testeado una vez, sirve para
  video y audio. Cualquier tipo nuevo (`BatchJob`, `PresetExportJob`) puede usar
  la misma infra agregando solo modelo + runner + lifecycle.
- **DRY/CR-3.7 cumplido**: no hay lógica de cola duplicada.
- **DIP/SOLID estrictos**: la UI no toca `_queue` privado; usa métodos del
  controller (`subscribe`, `cancel`, `retry`).
- **Tests independientes**: los runners se testean con `MockTransport`, los
  controllers con `_RecordingRunner` fake, las pantallas con
  `pilot.press/click` + injection del listener.

### Negativas

- **Over-dispatch del semáforo**: `_maybe_dispatch` de cada queue usa
  `len(self._active)` (su propio counter). Con dos queues puede haber hasta
  `2 × max_parallel_jobs` tareas creadas, pero solo `max_parallel_jobs` corren a
  la vez por el semáforo compartido. Funcionalmente correcto, ineficiente en uso
  de tasks. Mitigación futura: dispatcher global que mire la capacidad real
  antes de crear tasks.
- **`wait_for_job` sin timeout**: si el runner nunca emite terminal, el await es
  indefinido. Mitigado por el hecho de que la UI lo usa dentro de `run_worker`
  (cancelable). Mejora futura: timeout opcional + parámetro de la UI.
- **Detección de créditos por substring**: cuando el runner falla por créditos,
  persistimos el texto del error y la UI lo detecta buscando "credit" o "saldo".
  Frágil — mejorar con un campo `failure_kind: Literal[...]` en futuras
  iteraciones.
- **Hueco de idempotencia entre `create_tts_task` y persistir `task_id`**: si
  crash entre el POST exitoso y el upsert, queda en CREATING. El barrido
  `CREATING → FAILED` al arrancar evita estados fantasmas, pero el crédito ya se
  cobró. Sin idempotency key del lado de Kie no podemos cerrar este hueco al
  100%.

## Alternativas consideradas

- **Dos `QueueManager` separados** (uno por tipo): rechazado. Duplica la lógica
  de concurrencia, restore, listeners. Mantenimiento dual.
- **`QueueManager` heterogéneo** (`list[Any]`): rechazado. Pierde el type safety
  de mypy strict, complica los listeners y los handlers de eventos.
- **`status: str` en `RunnableJob` Protocol**: rechazado. `JobStatus` y
  `AudioJobStatus` son enums distintos; tratarlos como `str` perdía la garantía
  estática. Se resolvió moviendo las reglas de status al `JobLifecycle[T]`
  separado.
- **`JobUpdated[T]` genérico** (un único tipo de evento): rechazado. Las
  pantallas se suscriben al stream que les interesa; tener eventos separados
  (`JobUpdated`, `AudioJobUpdated`) evita `isinstance` checks en la UI y
  mantiene los tipos estrictos.

## Cumplimiento

- `docs/ARCHITECTURE.md` documenta las capas y los nuevos puertos.
- `docs/SPEC.md` describe el state machine de `AudioJob` y el protocolo de
  restore.
- `.importlinter` sigue verde con los nuevos módulos.
- 351 tests automatizados (cobertura 78%) cubren cola, runner, controllers, UI y
  restore.
- Rubber-duck consultado en las etapas críticas (1 y 3) detectó 6 issues
  bloqueantes que se incorporaron antes de mergear.
