<!-- markdownlint-disable MD013 -->
# Kie Avatar Studio — Copilot Instructions

## Objetivo

TUI local en Python (Textual) para automatizar producción de video con Kie.ai y
ElevenLabs. En esta rama conviven dos pipelines principales:

```text
video manual clásico
imagen + audio Kie + prompt -> Kling AI Avatar Pro -> outputs/<job_id>/final.mp4

workflow v2.0.0
modelo base + escenas + prompt -> VEO 3.1 (audio nativo)
                               -> concat local con FFmpeg + final_audio.mp3
                               -> voice changer opcional con ElevenLabs STS
```

Corre en una máquina personal, con colas durables, paralelismo controlado e
historial en SQLite. Toda la doc, comentarios y nombres de dominio están en
**español**; mantené ese idioma al generar texto.

`docs/SPEC.md` es la fuente de verdad del comportamiento. `docs/ARCHITECTURE.md`
describe la arquitectura real de la rama v2 y gana ante conflictos con el SPEC.
`docs/CODE_QUALITY.md` es la constitución: sus reglas `CR-X.Y` son las que cita
el agente `code-quality-reviewer`.

## Cómo correr y probar

Python **≥ 3.11** (usa `datetime.now(UTC)`, never `utcnow()` — CR-5.6).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"                  # pytest, pytest-asyncio, ruff, mypy,
                                         # pytest-cov, import-linter, pre-commit
pre-commit install                       # instala los hooks (ruff/mypy/imports/agent-sync)
cp .env.example .env                     # poner KIE_API_KEY

python -m kie_avatar_studio              # lanza la TUI
pytest -q                                # toda la suite
pytest tests/test_models.py::test_job_default_status -q   # un solo test
pytest tests/test_kie_client.py -q       # un solo archivo

./scripts/check.sh                       # ruff + mypy + import-linter + agent-sync + pytest+cov
./scripts/check.sh fast                  # versión rápida (sin mypy ni cov)
make check                               # alias de check.sh
make check-fast                          # alias de check.sh fast
make lint                                # ruff check .
make fmt                                 # ruff format .
make typecheck                           # mypy kie_avatar_studio
make imports                             # lint-imports
make test                                # pytest -q
make cov                                 # pytest + cobertura
pre-commit run --all-files               # hooks locales completos
```

`pytest-asyncio` está en modo **auto** (`pyproject.toml`); no decores los tests
async con `@pytest.mark.asyncio`. CI (`.github/workflows/ci.yml`) corre `ruff`,
`ruff format --check`, `mypy --strict`, `lint-imports` y `pytest -q`;
`scripts/check.sh` agrega `agent-sync` y, en modo full, cobertura.

### Fixtures de tests disponibles (`tests/conftest.py`)

Reutilizá estas antes de armar las tuyas:

- `tmp_settings` — `Settings` aislados en `tmp_path` con `ensure_dirs()` ya
  llamado.
- `jobs_db` — `JobsDB` inicializado contra `tmp_settings.db_path`.
- `mock_transport_factory` / `mock_kie_client` — `httpx.MockTransport` +
  `KieClient` con captura de requests. **Siempre** mockear HTTP así; cero
  llamadas reales a Kie.

### Trampas comunes (no "arreglar" sin entender)

- `tests/agent_fixtures/{bad,good}_feature.py` **violan reglas a propósito** —
  son la entrada de `test_agent_smoke.py` que valida al `code-quality-reviewer`.
  No las edites ni les apliques fixes de ruff; tienen `per-file-ignores`
  específicos en `pyproject.toml`.
- Ruff bloquea `requests` y `time.sleep` vía `flake8-tidy-imports.banned-api`
  (TID251) con el mensaje `Usa httpx async (CR-5.1).` /
  `Usa asyncio.sleep (CR-5.1).`. Si ves ese error, NO agregues un `# noqa`:
  cambiá al equivalente async.
- Cambios user-visible van bajo `[Unreleased]` en `CHANGELOG.md` (esquema L/M/S,
  ver `docs/VERSIONING.md`).

## Arquitectura (lo mínimo a respetar)

Cuatro capas con imports en una sola dirección (ver `docs/ARCHITECTURE.md` y
`docs/CODE_QUALITY.md` §1):

```text
ui          → app_layer, domain
app_layer   → domain                (NUNCA infra)   ← JobRunner, QueueManager
infra       → domain (solo DTOs)                    ← KieClient, JobsDB
domain      → nada interno                          ← models, policies, errors, events, ports
app.py      → infra, app_layer, ui, config          ← composition root (única excepción)
```

`.importlinter` codifica estos contratos; cualquier import nuevo que los rompa
hace fallar `pre-commit` y `scripts/check.sh`.

- `app.py` es el composition root: arma `Settings`, DBs, stores, clientes,
  runners, queues, controllers y limiters. **Sin singletons globales.**
- La UI nunca llama a `KieClient` ni a DBs; usa controllers (`VideosController`,
  `AudiosController`, `WorkflowController`, etc.) y listeners/eventos de cola.
- Flujo de un job de **video** (solo `JobRunner` muta `status`, y siempre
  persiste antes de seguir):

```text
queued → validating → (uploading_image ∥ creating_audio) → waiting_audio
       → creating_avatar → waiting_video → downloading → completed | failed | cancelled
```

`upload_image` y `create_audio` corren en paralelo con `asyncio.gather`. Entre
jobs el paralelismo lo limita `asyncio.Semaphore(settings.max_parallel_jobs)`.

### Subsistemas paralelos

Los subsistemas siguen el patrón
`job + runner + lifecycle + DB + queue + controller + pantalla`. No mezclar
lógica entre runners (CR-2.1).

**Video manual** (`VideosScreen` / hotkey **N**):

- `app_layer/job_runner.py` ejecuta `VideoJob` y es el único que muta
  `JobStatus`. Puede saltear upload/TTS cuando el job ya trae `image_url` y
  `audio_url`.
- `app_layer/videos_controller.py` recibe un `ImageAssetRef` discriminado desde
  `ImageCatalogController` para resolver imágenes subidas o generadas.
- `infra/db.py` persiste `VideoJob` en `data/jobs.db`.

**Audio TTS** (`Audios` / hotkey **A**):

- `app_layer/audio_job_runner.py` ejecuta
  `queued → validating → creating → polling → completed | failed | cancelled`.
- `infra/audio_jobs_db.py` persiste la cola; `infra/audios_db.py` persiste
  `GeneratedAudio`; `infra/audio_downloader.py` descarga MP3 lazy.
- `AudioJob` en `CREATING` al restart se marca `FAILED` por estado
  indeterminado; `POLLING` se reanuda por `task_id`.

**Imágenes** (`Imágenes` / hotkey **I**):

- `app_layer/image_job_runner.py` genera imágenes con Kie y revalida refs antes
  de crear tareas.
- `infra/images_db.py` guarda `UploadedImage`; `infra/generated_images_db.py`
  guarda `GeneratedImage`; `image_catalog_controller.py` unifica ambos stores
  para selectores.
- TTL: uploads Kie duran 24h; assets generados duran 14 días.

**Automatización v2** (`AutomationScreen` / hotkey **F**):

- `WorkflowRunner` orquesta un `WorkflowJob`, persiste header/steps en
  `WorkflowDB`, lanza steps en paralelo y notifica al queue.
- `WorkflowStepRunner` prepara o reutiliza `scene_image`, puede pasar por
  aprobación manual, llama `create_veo_video_task()`, espera con `VeoPoller`,
  descarga cada `step_x/video.mp4` y respeta `attached` solo para decidir si
  entra al concat final.
- Postproceso local: `workflow_concat.concatenate_workflow_videos()` usa FFmpeg
  para `final.mp4` y `final_audio.mp3`; `workflow_voice_changer` aplica
  ElevenLabs speech-to-speech opcional con `infra/elevenlabs_client.py`.
- `AtomicWorkflowManifestWriter` regenera `output_dir/workflow.json` en cada
  transición. SQLite es la fuente de verdad runtime; scripts externos deben
  consumir el JSON.
- Al restart, workflows no terminales se marcan `FAILED` y sus manifests se
  regeneran para evitar snapshots stale.

**Concurrencia en `app.py`**:

- `QueueManager` es genérico y recibe `JobLifecycle` + `event_factory`.
- Hay semáforos selectivos: `max_parallel_jobs` para jobs de video manual,
  `max_parallel_audio_jobs`, `max_parallel_image_jobs`,
  `max_parallel_video_jobs`, `max_parallel_upload_jobs`,
  `max_parallel_download_jobs` y `max_parallel_veo_jobs`.
- `workflow_queue` usa un limiter propio (`max_parallel_workflows`) para que el
  orquestador no consuma slots de sus propios sub-jobs.
- `LimitedKieGateway` limita operaciones Kie por tipo; `WorkflowStepRunner`
  además recibe limiters específicos para imagen/audio/video/download.

## Convenciones del repo

- **Async-only**: nada de `requests` ni `time.sleep`. Un único
  `httpx.AsyncClient` compartido en `KieClient`; `JobsDB` abre/cierra una
  conexión `aiosqlite` por operación (WAL mode).
- `from __future__ import annotations` y type hints en todos los módulos.
- `KieClient` es **HTTP puro** (sin validación ni lógica de negocio). Retries
  solo en 5xx con backoff exponencial; 4xx propaga como `KieClientError`.
  Descargas siempre por streaming.
- Validación de dominio en `domain/policies.py`. Límites duros de Kie: script y
  prompt ≤ 5000 chars, imagen png/jpg ≤ 10 MB, audio ≤ 100 MB / 5 min.
- Config con `pydantic-settings` leyendo `.env` (ver `.env.example`).
  `Settings.ensure_dirs()` crea
  `data/ outputs/ inputs/ presets/ batch_jobs/ workflows/ logs/`. No introducir
  un segundo mecanismo de config.
- Logs con `loguru`. Nunca loguear `KIE_API_KEY` ni `ELEVENLABS_API_KEY`;
  redactar `Authorization` como `Bearer ***`.
- Tests: cero llamadas reales a Kie — siempre `httpx.MockTransport`.

## Reglas de calidad operativas

No reescribas `docs/CODE_QUALITY.md` aquí; aplicá estas reglas específicas del
repo y citá el CR exacto cuando revises cambios:

- Los imports entre capas están forzados por `.importlinter`; cualquier cruce
  nuevo debe pasar por un `Protocol` en `domain/ports.py`.
- `KieClient` es HTTP puro: sin validación de dominio ni postproceso. 4xx
  propaga `KieClientError`; 5xx se reintenta con backoff; descargas por
  streaming.
- Los runners son los únicos que mutan status y siempre siguen write-ahead:
  asignar estado → `await repository.upsert(...)` → notificar listeners.
- Configuración solo por `Settings` (`pydantic-settings` + `.env`);
  `Settings.ensure_dirs()` crea
  `data/ outputs/ inputs/ presets/ logs/ batch_jobs/ workflows/`.
- Logs con `loguru`; nunca loguear `KIE_API_KEY`, `ELEVENLABS_API_KEY` ni
  headers sin redactar.
- `from __future__ import annotations`, type hints completos y
  `datetime.now(UTC)` en vez de `utcnow()`.
- Sin `requests`, `time.sleep` ni subprocess síncrono en hot path; Ruff lo
  bloquea con TID251.
- Cambios user-visible van bajo `[Unreleased]` en `CHANGELOG.md` según
  `docs/VERSIONING.md` (L/M/S → SemVer).
- No edites `tests/agent_fixtures/{bad,good}_feature.py`: violan reglas a
  propósito para validar el agente.

## Referencias

- `docs/CODE_QUALITY.md` — constitución del proyecto (reglas `CR-X.Y` no
  negociables).
- `docs/SPEC.md` — spec maestra (state machine, schemas, contratos, checklist).
- `docs/ARCHITECTURE.md` — reglas de capas, ciclo de vida del job, plantilla
  ADR.
- `docs/ROADMAP.md` — fase actual y siguiente.
- `docs/API_KIE.md` — endpoints, payloads y códigos de error de Kie.
- `docs/agents/code-quality-reviewer.prompt.md` — agente que revisa todo cambio
  (prompt canónico). Perfiles generados en
  `.opencode/agents/code-quality-reviewer.md` (OpenCode, `mode`/`permission`) y
  `.github/agents/code-quality-reviewer.agent.md` (Copilot CLI, `tools[]`).
  Sincronización validada por `scripts/check_agent_sync.sh`; regeneración con
  `scripts/build_agent_profiles.sh`.

## Code intelligence (CodeGraph MCP) — OBLIGATORIO

> **Esta no es una sugerencia. Es la primera línea de búsqueda del repo.** Las
> herramientas `codegraph_*` no son "una opción más" junto a grep/view: son el
> **default obligatorio** para toda pregunta sobre código en este proyecto. Caer
> a grep/view sin justificación es un anti-patrón.

El repo se sirve por **CodeGraph** vía MCP. Tanto Copilot CLI como OpenCode lo
cargan automáticamente desde el workspace:

| Cliente     | Archivo cargado                  |
| ----------- | -------------------------------- |
| Copilot CLI | `.mcp.json` (raíz del repo)      |
| OpenCode    | `opencode.jsonc` (raíz del repo) |

Índice local en `.codegraph/codegraph.db` (no commiteado). Tras un clone:

```bash
codegraph init -i        # crea + indexa
codegraph sync           # incremental tras cambios
codegraph status         # salud del índice
```

### Regla MUST-USE — sin excepciones por inercia

Si la pregunta o la tarea es **sobre código** (símbolos, callers/callees, flujo
"cómo X llega a Y", impacto de cambiar algo, exploración inicial de un módulo,
investigación de un bug, planificación de un refactor), la **primera tool que
disparás es `codegraph_*`**. No `grep`, no `view`, no `glob`. Punto.

> **MANDATORY pre-flight (no negociable, no opcional, no "esta vez sí").**
>
> Antes de **cualquier** batch que toque código, ejecutá este protocolo de 3
> pasos. Si saltás siquiera uno, estás violando la regla:
>
> 1. **Detectá la intención**: ¿la pregunta/tarea es sobre símbolos, flujos,
>    callers, callees, impacto, ubicación de código, comportamiento de un
>    módulo, o entender cómo algo funciona? → entonces es "sobre código".
> 2. **Mapeá a `codegraph_*`** usando la tabla de abajo. Si ninguna excepción
>    legítima aplica (ver §"Cuándo caer a grep/view/glob"), tu **primera** tool
>    call de ese batch DEBE ser `codegraph_*`. No `grep` "para arrancar", no
>    `glob` "para ubicar archivos", no `view` "para echar un vistazo".
> 3. **Solo después** de que CodeGraph te dio símbolos + ubicaciones, podés usar
>    `view` para abrir un archivo puntual, o `grep` para confirmar un string
>    literal. NUNCA al revés.
>
> "Ya sé dónde está", "es rápido con grep", "el archivo es chico", "conozco el
> repo" — **ninguna** de esas frases es una excepción. Si te encontrás
> escribiendo una justificación así, parate y empezá por `codegraph_*`.

Antes de cada batch de tool calls que toque código, hacé este check mental:

```text
¿Voy a usar grep/view/glob para buscar algo en el código?
   └─ SÍ ── ¿Hay un codegraph_* que responda lo mismo?
                ├─ SÍ ── Usá codegraph_*. Si después necesitás confirmar
                │        un string/comentario puntual, ahí sí grep/view.
                └─ NO ── Documentá brevemente por qué (string literal,
                         archivo no-Python fuera del índice, etc).
```

### Mapeo de intención → tool

| Intención                                                   | Tool obligatoria (primera llamada)                             |
| ----------------------------------------------------------- | -------------------------------------------------------------- |
| Contexto de una tarea / "¿cómo funciona X?" / planificación | `codegraph_context` (**PRIMARY** — usá esta antes que nada)    |
| Buscar símbolo por nombre                                   | `codegraph_search`                                             |
| Trazar flujo `X → Y` (request→handler, update→render, etc.) | `codegraph_trace`                                              |
| Quién llama a esto / a quién llama                          | `codegraph_callers` / `codegraph_callees`                      |
| Impacto de cambiar/renombrar un símbolo                     | `codegraph_impact`                                             |
| Ver código de varios símbolos relacionados                  | `codegraph_explore` (una sola llamada, no chained `view`s)     |
| Ver código de un símbolo puntual con su trail               | `codegraph_node` (con `includeCode=true` si necesitás el body) |

### Cuándo caer a grep/view/glob (excepciones legítimas, documentar)

Solo en estos casos:

1. **Strings o comentarios literales**: error messages, IDs CSS, claves de
   diccionarios, palabras dentro de logs — CodeGraph indexa símbolos, no strings
   arbitrarios.
2. **Archivos fuera del índice**: `.tcss`, `.md`, `.yml`, `.json`, configs
   (CodeGraph indexa Python + YAML, ver `codegraph_status`).
3. **Pending sync**: si `codegraph_status` reporta archivos pendientes y el
   símbolo que necesitás está ahí, usá `view` directo hasta que el sync corra.
4. **Confirmación rápida** (≤1 call): después de que CodeGraph te dio el
   símbolo + ubicación, podés abrir el archivo con `view` para ver el contexto
   visual completo si te ayuda. No es reemplazo, es complemento.

### Anti-patrones (errores observados, no repetir)

- ❌ `grep -rn "copy_to_clipboard" --include="*.py"` para encontrar callers → ✅
  `codegraph_callers` sobre el símbolo.
- ❌ `grep -rn "KIE_FILE_RETENTION_DAYS"` para evaluar impacto de un rename → ✅
  `codegraph_impact` sobre la constante.
- ❌ Cadena `grep` + `view` + `view` + `view` para entender un flujo → ✅
  `codegraph_context "descripción de la tarea"` UNA sola llamada.
- ❌ `view` con `view_range` repetidos en el mismo archivo → ✅
  `codegraph_explore` con los símbolos que te interesan.
- ❌ "Ya conozco el codebase, no necesito CodeGraph" (219 archivos indexados =
  sí, lo necesitás).

### Mantener el índice fresco

El MCP server arranca con file watcher (`codegraph serve --mcp`, sin
`--no-watch`) así que el grafo se actualiza solo ~1 s después de cada edit. Para
casos donde el watcher no alcanza — `git pull` con muchos cambios,
`git checkout` entre ramas, scaffolding masivo — usar el comando del cliente que
corresponda:

| Cliente     | Invocación                                                | Definición                               |
| ----------- | --------------------------------------------------------- | ---------------------------------------- |
| Copilot CLI | `/skill codegraph-sync`                                   | `.github/skills/codegraph-sync/SKILL.md` |
| OpenCode    | `/codegraph-sync` (opcional `full` para reindex completo) | `.opencode/commands/codegraph-sync.md`   |

Ambos hacen lo mismo: verifican instalación, deciden entre `sync` (incremental,
default) e `index` (full rebuild), y muestran el `status` antes/después.
Comandos equivalentes a mano:

```bash
codegraph status                # cuántos archivos pendientes + edad del índice
codegraph sync                  # incremental (default)
codegraph index                 # full reindex (solo tras renames masivos)
codegraph unlock                # liberar lock si quedó colgado
```

## Agente de revisión

Antes de pedir review, invoca al agente sobre tu cambio:

```text
TUI (OpenCode):  /agent code-quality-reviewer   ← .opencode/agents/code-quality-reviewer.md
CLI (Copilot):   /agent code-quality-reviewer   ← .github/agents/code-quality-reviewer.agent.md
```

El prompt vive como fuente única en
`docs/agents/code-quality-reviewer.prompt.md`. Cada sistema tiene su propio
frontmatter (OpenCode usa `mode`+`permission`, Copilot usa `tools[]` y sufijo
`.agent.md`). Para regenerar los perfiles desde la fuente:

```bash
./scripts/build_agent_profiles.sh
```

`scripts/check_agent_sync.sh` valida en pre-commit que el cuerpo de los tres
archivos coincida.

El agente devuelve un informe Markdown con veredicto `APROBADO` o
`CAMBIOS_REQUERIDOS`, citando la regla `CR-X.Y` exacta de cada hallazgo.
