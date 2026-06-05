# Code Quality — Constitución del proyecto

Reglas duras que rigen **todo cambio** en `Kie Avatar Studio`. El agente
`code-quality-reviewer` (`.opencode/agents/`, `.github/agents/`) las cita textualmente al
emitir su informe. Si una "solución rápida" viola alguna de estas reglas, primero se hace el
refactor que las restaura.

Convenciones:

- Toda la doc y los nombres de dominio están **en español**. Mantén el idioma del repo.
- `from __future__ import annotations` + type hints obligatorios.
- async-first. Prohibido `requests` y `time.sleep`.
- Python ≥ 3.11. `datetime.now(UTC)` (no `utcnow`).

---

## 1. Capas y dependencias (CR-1)

Forzado por `.importlinter`. Cualquier import nuevo que rompa esto **falla CI**.

```text
domain      → nada interno
infra       → domain (solo DTOs / errores)
app_layer   → domain                  (NUNCA infra)
ui          → domain, app_layer       (NUNCA infra)
app.py      → infra, app_layer, ui, config   (única excepción)
tests       → todo
```

Reglas operativas:

- `CR-1.1` El único lugar autorizado a construir clases concretas de `infra/` es
  `kie_avatar_studio/app.py`. `app_layer/` y `ui/` reciben los `Protocol`
  (`KieGateway`, `JobRepository`) por inyección.
- `CR-1.2` `domain/ports.py` declara los `Protocol` con `@runtime_checkable`. Cualquier
  nueva dependencia externa cruza por un nuevo `Protocol`, no por import directo.
- `CR-1.3` Sin imports relativos hacia arriba (`from ..app_layer import ...` desde
  `infra/` o `domain/` está prohibido).

## 2. SOLID aplicado (CR-2)

- `CR-2.1 SRP` — un módulo, una razón para cambiar. Si un archivo necesita dos cambios por
  razones distintas, **se parte**. Ejemplos vigentes que NO se mezclan:
  `KieClient` solo HTTP, `JobsDB` solo persistencia, `JobRunner` solo state machine,
  `QueueManager` solo concurrencia, `policies` solo validación.
- `CR-2.2 OCP` — extender, no editar. Agregar un nuevo modelo de Kie, una fuente de batch,
  o una pantalla nueva debe ser **otra clase/módulo**, no editar el contrato existente.
  El registry `MAIN_MENU` y el dict `_STATUS_SYNONYMS` son el patrón canónico.
- `CR-2.3 LSP` — cualquier doble de `KieClient` o `JobsDB` usado en tests respeta firma
  async, tipos de retorno y jerarquía de excepciones del `Protocol`.
- `CR-2.4 ISP` — si una pantalla solo necesita `enqueue`, **no recibe** `QueueManager`
  entero: declara un `Protocol` mínimo en `domain/ports.py` y depende de él.
- `CR-2.5 DIP` — `app_layer/` y `ui/` dependen únicamente de tipos de `domain/`. Importar
  `httpx`, `aiosqlite` o `textual` desde `domain/` o `app_layer/` está prohibido.

## 3. Clean code (CR-3)

- `CR-3.1` Funciones ≤ 30 líneas y ≤ 4 parámetros posicionales (DTO con muchos campos
  cuenta como 1).
- `CR-3.2` Archivos ≤ 300 líneas. Si crece, partir por responsabilidad.
- `CR-3.3` Sin números mágicos. Timeouts, tamaños y reintentos viven en `Settings` o en
  constantes nombradas en `domain/policies.py`. Ejemplos vigentes:
  `MAX_SCRIPT_CHARS`, `MAX_IMAGE_BYTES`, `_BACKOFF_BASE_SECONDS`,
  `_DOWNLOAD_CHUNK_BYTES`.
- `CR-3.4` Sin código muerto ni código comentado. Si no se usa, se borra.
- `CR-3.5` Comentar el **por qué** (decisión, contrato, invariante), no el qué.
- `CR-3.6` `TODO`s solo si referencian una fase del roadmap:
  `# TODO(Fase 2): confirmar shape de recordInfo`.
- `CR-3.7` Cero duplicación. Si dos sitios formatean un `VideoJob` igual, el formateo
  vive en `domain/` o en un helper de `ui/`.
- `CR-3.8` Nombres descriptivos en español. Sin abreviaturas crípticas (`q`, `mgr`, `tmp1`).
- `CR-3.9` Docstrings en español; describen contrato, no implementación.

## 4. Manejo de errores (CR-4)

- `CR-4.1` Usar la jerarquía tipada del dominio:
  `KieError → {KieClientError, KieServerError, KieTimeoutError}` y
  `JobValidationError`. Prohibido `ValueError`/`RuntimeError` ad-hoc en `infra/` o
  `app_layer/`.
- `CR-4.2` Prohibido `except Exception: pass` y `except:` desnudo. `JobRunner` es el
  **único** que captura excepciones para marcar `FAILED`; el resto propaga.
- `CR-4.3` 4xx no se reintenta; 5xx se reintenta con backoff exponencial limitado
  (`_BACKOFF_BASE_SECONDS`). Timeouts de polling → `KieTimeoutError`.

## 5. Async / Python (CR-5)

- `CR-5.1` Toda función que hace IO es `async`. Sin `requests`, sin `time.sleep`,
  sin `subprocess` síncrono en hot path.
- `CR-5.2` Un único `httpx.AsyncClient` por sesión de `KieClient`; siempre se cierra en
  `aclose()`.
- `CR-5.3` Streaming obligatorio en descargas (`download_file`); nada de cargar binarios
  enteros en memoria.
- `CR-5.4` `from __future__ import annotations` en todos los módulos.
- `CR-5.5` Type hints completos. `Any` solo con justificación en comentario adyacente
  (`# Any: payload heterogéneo de Kie, ver docs/API_KIE.md`).
- `CR-5.6` Datetime en UTC consciente: `datetime.now(UTC)`.

## 6. Estado y persistencia (CR-6)

- `CR-6.1` Solo `JobRunner` muta `VideoJob.status`. Toda transición sigue el patrón
  write-ahead: asignar estado → `await repository.upsert(job)` → notificar listeners.
- `CR-6.2` `JobsDB` usa `PRAGMA journal_mode=WAL`. Cada operación abre y cierra su propia
  conexión `aiosqlite`.
- `CR-6.3` Cambios de esquema se hacen con `ALTER TABLE` explícito en `db.py`, nunca
  borrando la DB. Documentar la migración con un comentario fechado.

## 7. Seguridad (CR-7)

- `CR-7.1` `KIE_API_KEY` jamás aparece en logs ni en mensajes de error. El header
  `Authorization` se redacta como `Bearer ***` antes de loguear.
- `CR-7.2` Rutas de salida se validan con `policies.is_path_inside(OUTPUTS_DIR)`. Nada
  escribe fuera de `OUTPUTS_DIR` sin pasar por esa validación (anti path-traversal en
  batch).
- `CR-7.3` `.env` no se versiona; `.env.example` sí. Pre-commit corre `ruff` y
  `import-linter` pero **no** debe abrir `.env` ni `data/jobs.db`.

## 8. Logs (CR-8)

- `CR-8.1` `loguru` con sink dual: stderr + `logs/kie-avatar-studio.log`
  (rotación 10 MB, retención 14 días).
- `CR-8.2` Cada log relacionado a un job incluye `job_id` en `extra`.
- `CR-8.3` En `DEBUG` se loguean payloads truncados a 1 KB. Nunca incluir secretos.

## 9. Tests (CR-9)

- `CR-9.1` Cero llamadas reales a Kie en cualquier nivel de tests. Siempre
  `httpx.MockTransport`.
- `CR-9.2` Cada bug fix trae el test que lo reproduce.
- `CR-9.3` Cobertura mínima: `domain/` ≥ 90 %, `app_layer/` ≥ 85 %, `infra/` ≥ 75 %.
- `CR-9.4` `pytest-asyncio` en modo **auto**: tests async sin decorador
  `@pytest.mark.asyncio`.
- `CR-9.5` Tests escriben solo en `tmp_path` o DB temporal. Nada de tocar `data/`,
  `outputs/`, `inputs/` reales.

## 10. UI (CR-10)

- `CR-10.1` La UI nunca importa `KieClient`, `JobsDB`, `httpx`, ni `aiosqlite`. Solo usa
  `queue.enqueue/cancel/retry`, `queue.add_listener`, y tipos de `domain/`.
- `CR-10.2` Refresco por listeners (no polling de UI).
- `CR-10.3` Estilos en `ui/styles.tcss` (CSS único). Sin CSS inline en widgets.
- `CR-10.4` Atajos globales en `app.py` (BINDINGS). Pantallas no registran atajos
  globales.

## 11. Config (CR-11)

- `CR-11.1` Toda config pasa por `Settings` (`pydantic-settings`) leyendo `.env`.
- `CR-11.2` Prohibido un segundo mecanismo (módulos sueltos con constantes que
  sobreescriben `.env`, etc.).
- `CR-11.3` `Settings.ensure_dirs()` se llama en el composition root antes de instanciar
  infra.

## 12. Commits y PR (CR-12)

- `CR-12.1` Mensaje en imperativo, en español, máximo 72 caracteres en el título.
  Ejemplos: `agrega validación de prompt en policies`, `corrige polling cuando recordInfo
  devuelve status pendiente`.
- `CR-12.2` Cada PR pasa `./scripts/check.sh` localmente
  (ruff + mypy + import-linter + pytest -q) antes de pedir review.
- `CR-12.3` El informe del agente `code-quality-reviewer` se adjunta al PR cuando hubo
  hallazgos relevantes.

## 13. Versionado (CR-13)

- `CR-13.1` Versión semántica `MAJOR.MINOR.PATCH` con etiquetas humanas:
  **L → MAJOR**, **M → MINOR**, **S → PATCH**. Reglas completas en
  [`docs/VERSIONING.md`](VERSIONING.md). La versión vive en `pyproject.toml`.
- `CR-13.2` Toda release bumpea `pyproject.toml`, mueve entradas de
  `[Unreleased]` a una sección con versión + fecha en `CHANGELOG.md`,
  y se tagea como `vX.Y.Z`. Commit final: `chore(release): vX.Y.Z`.
- `CR-13.3` Si en una release hay cambios de tamaños mixtos, se aplica
  **el más grande** (3 fixes + 1 feature = M; 1 feature + 1 breaking = L).

---

## Formato del informe del agente

El agente responde **solo** en este formato Markdown:

```text
# Code Quality Review — <título corto>

## Veredicto
APROBADO | CAMBIOS_REQUERIDOS

## Hallazgos
1. [CR-X.Y] <archivo>:<línea>  <descripción breve>
   Sugerencia: <opcional, patch o pseudocódigo>
2. ...

## Notas
<opcional, observaciones que no son violación pero conviene atender>
```

Reglas del agente:

- Cita siempre la regla (`CR-X.Y`) que justifica cada hallazgo.
- No comenta estilo trivial cubierto por `ruff` / `ruff-format`.
- No modifica código; solo emite informe.
- Si hay un fallo `CR-1.*` (capas), el veredicto **siempre** es `CAMBIOS_REQUERIDOS`.

## Tooling de respaldo

Lo no opinable lo automatizan los linters:

```text
ruff check . && ruff format --check .
mypy kie_avatar_studio
lint-imports                              # import-linter, lee .importlinter
pytest -q --cov=kie_avatar_studio --cov-fail-under=80
```

Todo encapsulado en `./scripts/check.sh` y replicado en `.pre-commit-config.yaml`.
