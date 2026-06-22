# Arquitectura

Complementa a `SPEC.md` con la arquitectura **real construida** y las reglas que
cualquier cambio debe respetar. Si esta página entra en conflicto con el SPEC,
esta gana hasta que el SPEC se actualice.

## Resumen

```text
ui          → app_layer, domain
app_layer   → domain                (NUNCA infra)
infra       → domain (solo DTOs)    (KieClient, JobsDB)
domain      → nada interno          (models, policies, errors, events, ports)
app.py      → infra, app_layer, ui, config     (composition root, única excepción)
```

Capas implementadas como paquetes Python con el mismo nombre. El **composition
root** es `kie_avatar_studio/app.py`: es el único módulo autorizado a construir
clases concretas de `infra/` e inyectarlas a `app_layer/` y `ui/`. El resto del
código depende solo de los `Protocol` declarados en `domain/ports.py`.

## Layout real

```text
kie_avatar_studio/
├── __init__.py
├── __main__.py
├── app.py                  composition root
├── config.py
├── domain/                 cero imports internos
│   ├── errors.py           KieError, KieClientError, KieServerError,
│   │                       KieTimeoutError, JobValidationError
│   ├── models.py           VideoJob, JobStatus, KieUploadResult,
│   │                       KieTaskCreated, TERMINAL/RESUMABLE
│   ├── policies.py         validate_job, normalize_task_status,
│   │                       extract_result_url, is_path_inside,
│   │                       constantes (MAX_SCRIPT_CHARS, MAX_IMAGE_BYTES,
│   │                       _BACKOFF_BASE_SECONDS, _DOWNLOAD_CHUNK_BYTES, …)
│   ├── events.py           JobUpdated, JobLog
│   └── ports.py            Protocols KieGateway, JobRepository
│                           (@runtime_checkable)
├── infra/                  solo importa domain
│   ├── kie_client.py       HTTP puro, retries 5xx, errores tipados
│   ├── db.py               aiosqlite + WAL + helpers _row_to_job/_job_to_row
│   └── logging.py
├── app_layer/              solo importa domain
│   ├── ids.py              new_job_id, sanitize_filename
│   ├── job_runner.py       state machine; depende de Protocols, no concretos
│   └── queue_manager.py    enqueue, cancel, retry, restore_pending,
│                           listeners sync + async
└── ui/                     solo importa domain + app_layer
    ├── menu.py             MenuItem registry (OCP)
    ├── styles.tcss         CSS único
    └── screens/
        └── main_menu.py
```

## Automatización v2.0.0 (VEO + postproceso)

Layout conceptual del subsistema de workflows tras la migración mayor:

```text
app_layer/
├── workflow_runner.py          orquestación global + manifest + notify
├── workflow_step_runner.py     state machine del step (`_run_veo`)
├── workflow_concat.py          concat de videos attached + extract audio
├── workflow_voice_changer.py   speech-to-speech final con ElevenLabs
└── veo_poller.py               polling dedicado de /api/v1/veo/record-info

infra/
├── kie_client.py               VEO generate + VEO record-info
├── elevenlabs_client.py        API directa /v1/speech-to-speech y /v2/voices
└── ffmpeg.py                   wrapper async del binario FFmpeg
```

Dependencias externas nuevas del pipeline:

- **ElevenLabs API**: voice selector + speech-to-speech final (`voice_changer`).
- **FFmpeg** (binario local): concat demuxer + extracción de audio
  (`final_audio.mp3`).

## Reglas de dependencia (contractuales)

```text
domain      no importa nada del paquete
infra       puede importar:  domain
app_layer   puede importar:  domain          ← NUNCA infra
ui          puede importar:  domain, app_layer
app.py      puede importar:  infra, app_layer, ui, config
tests/      puede importar:  todo
```

Estas reglas se documentan aquí y se **fuerzan en CI** con `import-linter`
(`.importlinter`). Cualquier import nuevo que las rompa hace fallar `pre-commit`
y el script `scripts/check.sh`.

## Vida de un job

```text
TUI (AudiosScreen / new_job) ─► AudioJob | VideoJob (queued, persistido)
              │
              ▼
       QueueManager.enqueue ─► tarea asyncio con semáforo GLOBAL
              │
              ▼
       Runner.run  (state machine específica del tipo)
              │ cada transición:
              │   1) job.status = nuevo_estado
              │   2) await repository.upsert(job)   ← write-ahead
              │   3) notificar listeners (event_factory)
              ▼
       outputs/<job_id>/final.mp4   (video)
       AudiosDB.upsert(GeneratedAudio)   (audio)
```

State machines (cada Runner es el ÚNICO que muta status del job que
corresponde):

```text
VideoJob (JobRunner):
queued → validating
       → uploading_image ∥ creating_audio  (asyncio.gather)
       → waiting_audio
       → creating_avatar
       → waiting_video
       → downloading
       → completed | failed | cancelled

AudioJob (AudioJobRunner):
queued → validating → creating → polling → completed | failed | cancelled
       ↑ resume idempotente: si task_id está poblado, no se re-crea en Kie.
```

Recuperación al arrancar (`QueueManager.restore_pending`):

- **Video**: jobs en
  `WAITING_AUDIO | WAITING_VIDEO | CREATING_AVATAR | DOWNLOADING` se re-encolan;
  el runner re-pide el `task_id` si ya existe.
- **Audio**: jobs en `QUEUED | POLLING` se re-encolan; los que quedaron en
  `CREATING` (estado indeterminado: el POST a Kie pudo o no haberse procesado)
  se barren a `FAILED` con error "indeterminado" para que el usuario decida.

## Concurrencia

- Una sola event-loop principal manejada por Textual.
- `JobRunner`, `AudioJobRunner` y todos los `QueueManager` viven en esa misma
  loop.
- `httpx.AsyncClient` único compartido en `KieClient`. Timeouts diferenciados
  para upload, json y download.
- `JobsDB` / `AudioJobsDB` abren y cierran conexión `aiosqlite` por operación;
  `PRAGMA journal_mode=WAL` evita bloqueos lector/escritor.
- **Paralelismo selectivo entre jobs Kie**: el composition root crea semáforos
  separados para video, audio TTS, imágenes, uploads y descargas
  (`max_parallel_video_jobs`, `max_parallel_audio_jobs`,
  `max_parallel_image_jobs`, `max_parallel_upload_jobs`,
  `max_parallel_download_jobs`). Esto permite subir throughput de imagen/video
  sin saturar TTS, que suele ser el endpoint más frágil.
- **Límite específico para VEO**: el workflow step runner usa además
  `max_parallel_veo_jobs` para no disparar demasiados renders caros al mismo
  tiempo (VEO 3.1 consume muchos más créditos que Nano Banana o uploads).

## Inversión de dependencias (DIP)

```python
# domain/ports.py
@runtime_checkable
class RunnableJob(Protocol):
    """Contrato mínimo para un job durable orquestable por QueueManager."""
    id: str
    def is_terminal(self) -> bool: ...
    def is_resumable(self) -> bool: ...

@runtime_checkable
class RunnableRunner(Protocol[T]):
    async def run(self, job: T) -> T: ...

@runtime_checkable
class JobLifecycle(Protocol[T_contra]):
    """Reglas cancel/retry/persist específicas por tipo de job."""
    def is_cancellable(self, job: T_contra) -> bool: ...
    def is_retryable(self, job: T_contra) -> bool: ...
    async def mark_cancelled(self, job: T_contra) -> None: ...
    async def reset_for_retry(self, job: T_contra) -> None: ...

@runtime_checkable
class KieGateway(Protocol):
    async def upload_file(self, ...): ...
    async def create_tts_task(self, ...): ...
    async def create_avatar_task(self, ...): ...
    async def get_task_detail(self, ...): ...
    async def create_veo_video_task(self, ...): ...
    async def get_veo_task_detail(self, ...): ...
    async def download_file(self, ...): ...
    async def aclose(self): ...

@runtime_checkable
class JobRepository(Protocol):
    async def init(self): ...
    async def upsert(self, job: VideoJob): ...
    async def get(self, job_id: str) -> VideoJob | None: ...
    async def list_recent(self, limit: int = 50) -> list[VideoJob]: ...
    async def list_by_status(self, status: JobStatus) -> list[VideoJob]: ...
    async def delete(self, job_id: str): ...

@runtime_checkable
class AudioJobRepository(Protocol):
    """Espejo de JobRepository pero para AudioJob (ADR-0007)."""
    # ... mismo shape ...
```

`JobRunner` / `AudioJobRunner` reciben `client: KieGateway` y
`repository: ...Repository` por inyección. Jamás importan `httpx` ni
`aiosqlite`. En `app.py`:

```python
# Composition root (extracto post ADR-0007)
db = JobsDB(settings.db_path)
audio_jobs_db = AudioJobsDB(settings.db_path)
kie = KieClient(settings)

# Semáforo compartido — clave para el límite GLOBAL.
capacity_limiter = asyncio.Semaphore(settings.max_parallel_jobs)

runner = JobRunner(settings, kie, db)
queue = QueueManager(
    settings, runner,
    event_factory=JobUpdated,
    lifecycle=VideoJobLifecycle(db),
    capacity_limiter=capacity_limiter,
)

audio_runner = AudioJobRunner(settings, kie, audio_jobs_db, audios_db)
audio_queue = QueueManager(
    settings, audio_runner,
    event_factory=AudioJobUpdated,
    lifecycle=AudioJobLifecycle(audio_jobs_db),
    capacity_limiter=capacity_limiter,  # MISMO Semaphore
)
```

Los tests pueden reemplazar `KieClient` / `JobsDB` / `AudioJobsDB` por dobles
in-memory siempre que cumplan los `Protocol` (validable con
`isinstance(obj, KieGateway)`).

## Flujo workflow v2.0.0

```text
WorkflowRunner
  ├─ resolve voice/base + persist header
  ├─ lanza N WorkflowStep en paralelo
  │    └─ WorkflowStepRunner
  │         ├─ prepara/reusa scene_image
  │         ├─ opcional: awaiting_approval
  │         ├─ create_veo_video_task()
  │         ├─ veo_poller.poll_veo_task_for_url()
  │         └─ download_kie_asset() -> step_x/video.mp4
  └─ postproceso final
       ├─ workflow_concat.concatenate_workflow_videos()
       │    ├─ FFmpeg concat -> final.mp4
       │    └─ FFmpeg extract audio -> final_audio.mp3
       └─ workflow_voice_changer.apply_voice_changer() opcional
            └─ ElevenLabsClient.speech_to_speech() -> voice_changed_audio.mp3
```

Notas de diseño:

- `StepType` (`a-roll` / `b-roll`) se mantiene como semántica editorial/UI, pero
  ambos tipos convergen al mismo backend de render: **VEO 3.1**.
- `attached` decide si el clip participa del concat final; nunca impide la
  generación ni la descarga individual del step.
- El postproceso es **local** y deliberadamente queda fuera de `KieClient`: Kie
  termina en URL de video; desde ahí manda `workflow_concat` y, si aplica,
  `workflow_voice_changer`.

## Manejo de errores

```text
4xx                       → KieClientError              (no retry)
5xx (tras backoff x3)     → KieServerError              (no retry)
Timeout de polling         → KieTimeoutError
Validación de dominio      → JobValidationError | AudioValidationError
```

- `JobRunner` y `AudioJobRunner` son los **únicos** que capturan excepciones
  para marcar `FAILED`; las demás capas dejan propagar la jerarquía
  `KieError`/`JobValidationError`. Prohibido `except Exception: pass` en
  cualquier capa.

## Atajos y comportamiento de UI

- La UI no llama a `KieClient`, `JobsDB` ni `AudioJobsDB`. Solo usa los
  controllers:
  - `audios_controller.enqueue_generation(...)`, `.cancel(id)`, `.retry(id)`,
    `.subscribe(cb)`, `.delete_job(id)`.
  - `history_controller.list_recent_entries(...)`, `.subscribe(cb)`.
  - (Video tendrá su controller equivalente cuando se implemente la pantalla
    `new_job`.)
- Los listeners del queue son sync y se ejecutan dentro de `_notify`. Para
  evitar re-entrada, las pantallas convierten el evento en un `Message` de
  Textual via `post_message` y manejan el refresh en su propio turno del event
  loop.
- El menú principal vive en `ui/menu.py` como **registry declarativo**. La
  fuente de verdad es `MAIN_MENU_GROUPS: tuple[MenuSection, ...]` (4 secciones:
  Crear / Monitoreo / Biblioteca / Sistema). Cada `MenuSection` agrupa items
  relacionados y aporta su `label` para renderizar un header visual.
  `MAIN_MENU: tuple[MenuItem, ...]` es la vista flat derivada (orden de
  aparición) que `app.py` consume para registrar atajos globales vía
  `MENU_BY_ID`. Agregar opciones se hace declarando un `MenuItem` más dentro de
  la `MenuSection` que corresponda; nunca editando el dispatcher.
- `Ctrl+C` dispara `queue.drain()` y `audio_queue.drain()` antes de cerrar;
  `KieClient.aclose()` se llama en `on_unmount`.

## Plantilla de ADR

```markdown
# NNNN. Título corto

Fecha: YYYY-MM-DD Estado: Propuesto | Aceptado | Reemplazado por #NNNN

## Contexto

## Decisión

## Consecuencias

## Alternativas consideradas
```

Cada decisión arquitectónica grande se archiva en `docs/adr/NNNN-titulo.md`.
