# Kie Avatar Studio - Spec del proyecto

Documento maestro. Léelo de arriba a abajo antes de tocar código.
Última revisión: 2026-06-15

---

## 1. Propósito

App local en Python con interfaz **TUI (Textual)** para automatizar producción de video con las APIs de **Kie.ai**. Hoy conviven dos pipelines:

```text
video manual clásico
script + imagen + voz + prompt  -->  audio TTS + video Kling Avatar

workflow v2.0.0
modelo base + escenas + prompt  -->  VEO 3.1 (audio nativo)
                                  -->  concat local + extracción de audio
                                  -->  voice changer opcional (ElevenLabs)
```

Optimizada para correr en una máquina personal sin exponer la API key, soportar **lotes** y **paralelismo controlado**, mantener un **historial persistente**, y servir de base reutilizable para una futura UI web/Electron sin reescribir la lógica.

---

## 2. Objetivos y no-objetivos

### Objetivos

- Crear y ejecutar un job de video punta a punta sin intervención manual entre pasos.
- Procesar varios jobs en cola con un límite configurable de paralelismo.
- Persistir cada job y su estado en SQLite local.
- Mostrar progreso live y logs por job.
- Soportar procesamiento por lotes desde una carpeta `batch_jobs/`.
- Hacer fácil sustituir la TUI por otra UI (web, Electron) sin cambiar la lógica de negocio.

### No-objetivos (por ahora)

- Servidor multi-usuario o multi-tenant.
- Autenticación por usuario.
- UI web/Electron en la fase 1.
- Edición manual tipo timeline / subtítulos / color grading. El post-proceso automático de workflows (concat + extracción de audio + voice changer) sí forma parte del producto.
- Callbacks HTTP de Kie (se hace polling).

---

## 3. Restricciones del proveedor (Kie.ai)

```text
script max chars        : 5000
prompt max chars        : 5000
imagen formatos         : jpeg, png
imagen tamaño max       : 10 MB
audio tamaño max        : 100 MB
audio duración max      : 5 min
audio formatos          : audio/mpeg, audio/wav, audio/x-wav, audio/aac, audio/mp4, audio/ogg
modelo TTS              : elevenlabs/text-to-speech-multilingual-v2
modelo Avatar           : kling/ai-avatar-pro
modelos workflow video  : veo3, veo3_fast, veo3_lite
endpoint upload         : POST https://kieai.redpandaai.co/api/file-stream-upload
endpoint createTask     : POST https://api.kie.ai/api/v1/jobs/createTask
endpoint recordInfo     : GET  https://api.kie.ai/api/v1/jobs/recordInfo?taskId=<id>
endpoint veo generate   : POST https://api.kie.ai/api/v1/veo/generate
endpoint veo polling    : GET  https://api.kie.ai/api/v1/veo/record-info?taskId=<id>
endpoint sts directo    : POST https://api.elevenlabs.io/v1/speech-to-speech/<voice_id>
```

Notas workflow v2.0.0:

- `POST /api/v1/veo/generate` usa endpoints propios de VEO; **no** pasa por `/jobs/createTask`.
- Polling VEO usa `data.successFlag` (`0=generando`, `1=success`, `2=failed`, `3=upstream failed`) y extrae el MP4 desde `data.response.resultUrls[]`.
- El schema JSON v2 introduce `pre_settings.veo`, `pre_settings.voice_changer` y `run[].attached`.
- `audio_language`, `voice_preset_id`/`voice_preset` e `i2v_duration_seconds` quedan deprecated y el loader los conserva solo por backward compat.

Polling sugerido:

```env
POLL_INTERVAL_SECONDS=10
TASK_TIMEOUT_SECONDS=1800
```

---

## 4. Arquitectura

### 4.1 Principios

- **Capas claras**: la TUI no conoce httpx ni Kie; el dominio no conoce SQLite.
- **Asíncrono de punta a punta** (`asyncio` + `httpx.AsyncClient` + `aiosqlite`).
- **Inyección por composición**: las dependencias (`KieClient`, `JobsDB`) se pasan al `JobRunner` y `QueueManager`; no hay singletons globales.
- **Eventos > polling de UI**: la cola emite eventos que la TUI escucha.
- **Idempotencia**: cada job tiene un `id` único y todos los upserts en DB se hacen por `id`.
- **Reentrancia**: si la app se cae, al reabrir debe poder reanudar jobs `WAITING_*`.
- **Sin secretos en código**: API keys vienen de `.env` validado con pydantic-settings.

### 4.2 Capas

```text
┌──────────────────────────────────────────────────────────┐
│ UI (Textual)                                             │
│   screens/main_menu, new_job, queue, history, ...        │
│   solo dispatch de acciones y render de estado           │
├──────────────────────────────────────────────────────────┤
│ Application                                              │
│   QueueManager: orquesta jobs en paralelo                │
│   JobRunner   : ejecuta UN job (state machine)           │
│   BatchLoader : convierte carpetas en VideoJob[]         │
├──────────────────────────────────────────────────────────┤
│ Domain                                                   │
│   models.py   : VideoJob, JobStatus, Kie* DTOs           │
│   policies    : validaciones de Kie (size, chars, etc.)  │
├──────────────────────────────────────────────────────────┤
│ Infrastructure                                           │
│   kie_client.py (httpx async)                            │
│   db.py        (aiosqlite repo)                          │
│   fs / logs    (loguru)                                  │
└──────────────────────────────────────────────────────────┘
```

### 4.3 Diagrama de componentes

```text
            ┌────────────┐
            │  Textual   │
            │   App      │
            └─────┬──────┘
                  │ acciones (enqueue, cancel, retry)
                  ▼
           ┌──────────────┐ listeners(job)  ┌──────────────┐
           │ QueueManager │ ───────────────►│  UI Screens  │
           └─────┬────────┘                 └──────────────┘
                 │ run(job)
                 ▼
           ┌──────────────┐
           │  JobRunner   │
           └────┬────┬────┘
                │    │
        upload  │    │  TTS+Video
                ▼    ▼
           ┌──────────────┐   ┌──────────────┐
           │  KieClient   │   │   JobsDB     │
           │   (httpx)    │   │ (aiosqlite)  │
           └──────────────┘   └──────────────┘
```

---

## 5. Estructura de carpetas (definitiva)

```text
KieAvatarStudio/
├── README.md
├── pyproject.toml
├── requirements.txt
├── .env.example
├── .gitignore
├── docs/
│   ├── SPEC.md                  # este documento
│   ├── ARCHITECTURE.md          # detalle de capas + ADRs
│   ├── ROADMAP.md               # plan por fases
│   ├── API_KIE.md               # cheatsheet endpoints + ejemplos curl
│   └── adr/
│       ├── 0001-async-stack.md
│       ├── 0002-sqlite-aiosqlite.md
│       └── 0003-textual-tui.md
├── kie_avatar_studio/
│   ├── __init__.py
│   ├── __main__.py
│   ├── app.py
│   ├── config.py
│   ├── utils.py
│   ├── domain/
│   │   ├── __init__.py
│   │   ├── models.py
│   │   ├── policies.py
│   │   └── events.py
│   ├── infra/
│   │   ├── __init__.py
│   │   ├── kie_client.py
│   │   ├── db.py
│   │   └── files.py
│   ├── app_layer/
│   │   ├── __init__.py
│   │   ├── job_runner.py
│   │   ├── queue_manager.py
│   │   └── batch_loader.py
│   └── ui/
│       ├── __init__.py
│       ├── styles.tcss
│       └── screens/
│           ├── __init__.py
│           ├── main_menu.py
│           ├── new_job.py
│           ├── queue.py
│           ├── job_detail.py
│           ├── history.py
│           ├── presets.py
│           └── settings.py
├── presets/
│   ├── voices.json
│   └── prompts.json
├── inputs/        # imágenes/scripts sueltos del usuario
├── outputs/       # un subdirectorio por job
├── batch_jobs/    # video_001/, video_002/, ...
├── logs/          # rotados por loguru
├── data/          # jobs.db
└── tests/
    ├── conftest.py
    ├── test_models.py
    ├── test_policies.py
    ├── test_utils.py
    ├── test_db.py
    ├── test_kie_client.py
    ├── test_job_runner.py
    └── test_queue_manager.py
```

Nota: el esqueleto actual usa rutas planas (`kie_client.py`, `job_runner.py`, etc.). En la primera tarea del Roadmap se reorganiza a este layout.

---

## 6. Modelo de dominio

### 6.1 `JobStatus` (state machine VideoJob)

```text
queued
  └─► validating
        └─► uploading_image ─┐
        └─► creating_audio  ─┤
                             ├─► waiting_audio
                             ▼
                        creating_avatar
                             │
                             ▼
                        waiting_video
                             │
                             ▼
                        downloading
                             │
                             ▼
                        completed
                             │
                             ▼
                     (terminal: completed | failed | cancelled)
```

Reglas:

- Solo `JobRunner` muta `status` de un `VideoJob`.
- Las transiciones siempre se persisten antes de continuar (write-ahead).
- Cancelación: la UI delega a `queue.cancel(id)` que consulta
  `VideoJobLifecycle.is_cancellable` (rechaza `downloading` y los
  terminales).

### 6.1.bis `AudioJobStatus` (state machine AudioJob)

Cola separada introducida en ADR-0007. State machine más simple
porque no hay upload de imagen ni download local (solo URL Kie).

```text
queued
  └─► validating
        └─► creating
              │
              ▼
        polling (loop hasta success | failed)
              │
              ▼
        completed
              │
              ▼
       (terminal: completed | failed | cancelled)
```

Reglas:

- Solo `AudioJobRunner` muta `status` de un `AudioJob`.
- Reanudable: `QUEUED | POLLING`. `CREATING` queda fuera porque sin
  `task_id` persistido no podemos saber si el POST llegó a Kie →
  al arrancar se barren a `FAILED` con error "indeterminado".
- Resume idempotente: si `task_id` ya está poblado, el runner reusa
  ese task en Kie en vez de crear uno nuevo (evita doble cobro).
- Idempotencia de salida: el `GeneratedAudio` final usa el mismo
  `id` que el `AudioJob` → un reintento exitoso hace upsert sobre
  la misma fila en `generated_audios`.
- Las dos colas (`queue` para video, `audio_queue` para audio)
  comparten un único `asyncio.Semaphore(max_parallel_jobs)` →
  el límite global de paralelismo SIEMPRE se respeta.

### 6.2 `VideoJob`

```python
class VideoJob(BaseModel):
    id: str
    script: str
    image_path: str
    prompt: str
    voice: str
    status: JobStatus = JobStatus.QUEUED
    image_url: str | None = None
    audio_task_id: str | None = None
    audio_url: str | None = None
    video_task_id: str | None = None
    video_url: str | None = None
    output_path: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime
```

Validaciones en `domain/policies.py`:

```python
def validate_job(job: VideoJob) -> None:
    if not (1 <= len(job.script) <= 5000): raise ValueError(...)
    if not (1 <= len(job.prompt) <= 5000): raise ValueError(...)
    if Path(job.image_path).suffix.lower() not in {".png", ".jpg", ".jpeg"}: raise ...
    if Path(job.image_path).stat().st_size > 10 * 1024 * 1024: raise ...
```

### 6.3 Eventos (`domain/events.py`)

```python
@dataclass
class JobUpdated:
    job: VideoJob

@dataclass
class JobLog:
    job_id: str
    level: str
    message: str
```

`QueueManager` mantiene `add_listener(cb)` y los emite tras cada cambio de estado.

### 6.4 `UploadedImage` (galería persistente — ADR-0005)

```python
class UploadedImage(BaseModel):
    id: str
    label: str
    local_path: str
    kie_url: str
    kie_file_path: str
    file_size: int
    mime_type: str
    uploaded_at: datetime
    # helpers: expires_at(retention_days), is_expired(...), time_left(...)
```

Persistido en tabla `uploaded_images` de `data/jobs.db` (ver §7.1).

### 6.5 `GeneratedAudio` + `VoiceSettings` + `KieVoice` (Fase 2.2c — ADR-0006)

```python
class VoiceSettings(BaseModel):
    """Inputs opcionales del endpoint TTS (rangos del OpenAPI spec)."""
    stability: float | None         # 0.0 - 1.0   (default Kie: 0.5)
    similarity_boost: float | None  # 0.0 - 1.0   (default Kie: 0.75)
    style: float | None             # 0.0 - 1.0   (default Kie: 0)
    speed: float | None             # 0.7 - 1.2   (default Kie: 1.0)
    language_code: str | None       # ISO 639-1   (solo turbo/flash v2.5)


class GeneratedAudio(BaseModel):
    id: str
    label: str
    script: str
    voice_id: str
    voice_settings: VoiceSettings | None
    kie_url: str
    kie_file_path: str
    file_size: int | None
    mime_type: str | None
    duration_seconds: float | None
    generated_at: datetime
    # helpers: expires_at(retention_days), is_expired(...), time_left(...)


class KieVoice(BaseModel):
    """Voz del catálogo built-in (67 entradas en `kie_voice_catalog.BUILTIN_VOICES`)."""
    voice_id: str
    label: str
    description: str = ""
    # propiedades: preview_url (https://static.aiquickdraw.com/...), display_name
```

Persistido en tabla `generated_audios` de `data/jobs.db`. `voice_settings` se
serializa como JSON nullable. Validaciones en `policies.validate_tts_script`,
`validate_voice_id(allow_custom)`, `validate_voice_settings`.

Retención (política Kie):
- `KIE_GENERATED_RETENTION_DAYS = 14` — audios TTS y videos.
- `KIE_UPLOAD_RETENTION_HOURS = 24` — uploads (imágenes via file-stream-upload).
- `KIE_FILE_RETENTION_DAYS = 14` — alias backwards-compat usado por `UploadedImage`
  hasta migración pendiente (ver ADR-0006 §"Aclaración sobre retención").

---

## 7. Persistencia

### 7.1 Esquema SQLite

```sql
CREATE TABLE IF NOT EXISTS jobs (
  id            TEXT PRIMARY KEY,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  status        TEXT NOT NULL,
  script        TEXT NOT NULL,
  image_path    TEXT NOT NULL,
  prompt        TEXT NOT NULL,
  voice         TEXT NOT NULL,
  image_url     TEXT,
  audio_task_id TEXT,
  audio_url     TEXT,
  video_task_id TEXT,
  video_url     TEXT,
  output_path   TEXT,
  error         TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_status     ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at);
```

### 7.2 Repositorio `JobsDB`

API mínima:

```python
async def init() -> None: ...
async def upsert(job: VideoJob) -> None: ...
async def get(job_id: str) -> VideoJob | None: ...
async def list_recent(limit: int = 50) -> list[VideoJob]: ...
async def list_by_status(status: JobStatus) -> list[VideoJob]: ...
async def delete(job_id: str) -> None: ...
```

Reglas:

- `aiosqlite` con `PRAGMA journal_mode = WAL` para no bloquear lectura/escritura concurrente.
- Cada operación abre/cierra su conexión (simple y seguro para esta escala).
- Migraciones manuales por ahora: cualquier cambio de esquema añade columnas con `ALTER TABLE`.

---

## 8. Cliente Kie (`infra/kie_client.py`)

Contrato público:

```python
class KieClient:
    async def upload_file(file_path, upload_path="images/avatar-models", file_name=None) -> KieUploadResult
    async def create_tts_task(text, voice, model="elevenlabs/text-to-speech-multilingual-v2") -> KieTaskCreated
    async def create_avatar_task(image_url, audio_url, prompt, model="kling/ai-avatar-pro") -> KieTaskCreated
    async def get_task_detail(task_id) -> dict
    async def create_veo_video_task(
        prompt,
        *,
        image_urls=None,
        model="veo3_fast",
        generation_type="FIRST_AND_LAST_FRAMES_2_VIDEO",
        aspect_ratio="9:16",
        resolution="720p",
        duration=8,
        enable_translation=True,
        watermark=None,
    ) -> KieTaskCreated
    async def get_veo_task_detail(task_id) -> dict
    async def download_file(url, output_path) -> Path
```

Reglas:

- `httpx.AsyncClient` con `timeout=60s` (conexión 15s) y `Authorization: Bearer`.
- Retries simples para 5xx con backoff exponencial (3 intentos, base 1s).
- Errores 4xx no se retryean; se propagan como `KieClientError`.
- Streaming en `download_file` para no cargar el video en memoria.
- Sin lógica de negocio; solo HTTP.

DTOs:

```python
class KieUploadResult(BaseModel):
    file_name: str; file_path: str; download_url: str; file_size: int; mime_type: str

class KieTaskCreated(BaseModel):
    task_id: str
```

---

## 9. JobRunner (state machine concreta)

Pseudocódigo final esperado:

```python
async def run(job: VideoJob) -> VideoJob:
    try:
        await set_status(job, VALIDATING);   validate_job(job)
        upload = create_task(upload_image(job))
        audio  = create_task(create_audio(job))
        image_url, audio_url = await gather(upload, audio)

        await set_status(job, CREATING_AVATAR)
        job.video_task_id = (await client.create_avatar_task(image_url, audio_url, job.prompt)).task_id
        await db.upsert(job)

        await set_status(job, WAITING_VIDEO)
        job.video_url = await poll_for_url(job.video_task_id, kind="video")
        await db.upsert(job)

        await set_status(job, DOWNLOADING)
        job.output_path = str(await client.download_file(job.video_url, outputs/job.id/"final.mp4"))

        await set_status(job, COMPLETED)
    except Exception as exc:
        job.error = str(exc); await set_status(job, FAILED)
    return job
```

Polling tolerante (`poll_for_url`):

- Lee `data.status` y normaliza a {`pending`, `running`, `success`, `failed`}.
- Para éxito busca `audio_url`/`video_url`/`result_url`/`output.url`.
- Respeta `TASK_TIMEOUT_SECONDS` con `asyncio.sleep(POLL_INTERVAL_SECONDS)`.
- Loguea cada poll en `DEBUG`.

---

## 10. QueueManager

- `asyncio.Semaphore(MAX_PARALLEL_JOBS)`.
- `deque` de pendientes + `dict` de activos.
- API:

```python
def enqueue(job: VideoJob) -> None
def cancel(job_id: str) -> bool
def retry(job_id: str) -> bool
async def drain() -> None
def add_listener(cb: Callable[[VideoJob], None]) -> None
```

- Recuperación al arranque: cargar de DB todos los jobs en estados `WAITING_AUDIO|WAITING_VIDEO|CREATING_*|DOWNLOADING` y re-encolarlos (idempotente, el runner sabe re-pedir el `task_id` si ya existe).



### 10.1 Automatización (workflows v2.0.0)

La automatización dejó de dividir los steps entre Avatar Pro, Kling 3.0 y
TTS por separado. Desde **v2.0.0**, todos los steps renderizan video con
**VEO 3.1** y el audio se trata como una preocupación de post-proceso.

#### State machine del workflow

```text
WorkflowJob
queued
  └─► preparing_base
        └─► running
              ├─► awaiting_approval      (solo si scene_approval_mode=manual)
              ├─► completed
              ├─► partially_failed
              ├─► failed
              └─► cancelled
```

#### Flow de un step

```text
WorkflowStep
queued
  └─► preparing
        └─► scene_image opcional (Nano Banana / reuso de base)
              └─► awaiting_approval?
                    └─► rendering (VEO 3.1)
                          └─► downloading (video.mp4)
                                └─► completed | failed | cancelled
```

Reglas:

- `WorkflowStepRunner` unifica el runtime en `_run_veo()`; ya no existen
  ramas separadas por `a-roll`/`b-roll` para elegir backend de video.
- `type=a-roll` y `type=b-roll` siguen existiendo, pero ahora describen el
  **rol editorial** de la escena (talento vs recurso), no el motor de render.
- El prompt del step alimenta directamente a VEO. Cuando la escena necesita
  una `scene_image`, primero se genera/reutiliza la imagen y luego se pasa
  en `imageUrls[]` con `generationType=FIRST_AND_LAST_FRAMES_2_VIDEO`.
- `attached=true` (default) indica que el `video.mp4` del step entra al
  reel final; `attached=false` lo deja solo como output individual.

#### Post-proceso al terminar los steps

```text
steps completed
  └─► concatenar videos attached         -> outputs/<wf_id>/final.mp4
        └─► extraer audio con FFmpeg     -> outputs/<wf_id>/final_audio.mp3
              └─► speech-to-speech opcional
                    (pre_settings.voice_changer)
                    -> outputs/<wf_id>/voice_changed_audio.mp3
```

Detalles del pipeline:

- La concatenación usa FFmpeg local y omite steps no attached o sin
  `video.mp4` descargado.
- Si hay un solo clip attached, se copia tal cual a `final.mp4` y luego se
  extrae su audio igualmente.
- `pre_settings.voice_changer` apunta a ElevenLabs directo
  (`speech-to-speech`) y trabaja sobre `final_audio.mp3`, nunca por step.
- Cada transición persiste DB + manifest atómico (`workflow.json`) antes de
  notificar a la UI.

#### Schema JSON v2 resumido

```json
{
  "pre_settings": {
    "model_creation": { "method": "prompt" },
    "veo": {
      "model": "veo3_fast",
      "aspect_ratio": "9:16",
      "resolution": "720p",
      "duration": 8,
      "enable_translation": true,
      "watermark": null
    },
    "voice_changer": {
      "voice_id": "voice_123",
      "model_id": "eleven_multilingual_sts_v2",
      "remove_background_noise": true,
      "output_format": "mp3_44100_128"
    }
  },
  "run": [
    {
      "step": 1,
      "type": "a-roll",
      "attached": true,
      "prompt": "Persona a cámara, audio nativo VEO"
    }
  ]
}
```

Campos deprecated aceptados solo para compatibilidad de carga:

- `pre_settings.audio_language`
- `pre_settings.voice_preset_id` / alias `voice_preset`
- `pre_settings.i2v_duration_seconds`

---

## 11. BatchLoader

Convierte carpetas `batch_jobs/video_NNN/` en `VideoJob[]`.

Formato esperado por carpeta:

```text
batch_jobs/video_001/
  script.txt           obligatorio
  modelo.(png|jpg)     obligatorio (primer match)
  prompt.txt           opcional, si no hay -> DEFAULT_PROMPT
  voice.txt            opcional, si no hay -> DEFAULT_VOICE
  meta.json            opcional, override puntual { "voice": "...", "prompt": "..." }
```

Salida en `outputs/video_001/`:

```text
final.mp4
audio.json   # task detail del audio
video.json   # task detail del video
job.json     # snapshot final del VideoJob
```

---

## 12. UI (Textual)

### 12.1 Pantallas

```text
main_menu     [N]uevo  [B]atch  [Q]ueue  [H]istorial  [P]resets  [C]onfig  [X]Salir
new_job       form: script, image picker, voice select, prompt select
queue         DataTable live: id, status, t, output
job_detail    logs en vivo + estado + acciones (retry, cancel, open folder)
history       DataTable read-only de finalizados
presets       editar voices.json / prompts.json
settings      editar .env asistido (no se sobreescribe sin confirmar)
```

### 12.2 Reglas de UI

- La UI **nunca** llama directo a `KieClient` ni a `JobsDB`.
- Toda acción es: `app.queue.enqueue(job)`, `app.queue.cancel(id)`, etc.
- Refresco por listeners (no polling de UI).
- Atajos globales: `q` salir, `?` ayuda, `g` ir a Queue, `h` ir a Historial.

### 12.3 Theming

Archivo único `kie_avatar_studio/ui/styles.tcss`. No CSS inline en cada widget.

---

## 13. Configuración

`config.py` con `pydantic-settings`:

```env
KIE_API_KEY=
KIE_API_BASE=https://api.kie.ai
KIE_UPLOAD_BASE=https://kieai.redpandaai.co
ELEVENLABS_API_KEY=
MAX_PARALLEL_JOBS=2
MAX_PARALLEL_VEO_JOBS=1
POLL_INTERVAL_SECONDS=10
TASK_TIMEOUT_SECONDS=1800
DEFAULT_VOICE=EkK5I93UQWFDigLMpZcX
DEFAULT_PROMPT=Mirada a cámara, expresión natural, gestos suaves, tono confiado.
FFMPEG_PATH=ffmpeg
DATA_DIR=./data
OUTPUTS_DIR=./outputs
INPUTS_DIR=./inputs
PRESETS_DIR=./presets
LOGS_DIR=./logs
LOG_LEVEL=INFO
```

Reglas:

- `Settings()` valida tipos al cargar.
- `ensure_dirs()` crea las carpetas si faltan.
- Nunca imprimir `KIE_API_KEY` en logs.

---

## 14. Logging

- `loguru` con sink doble: stderr + archivo rotado `logs/kie-avatar-studio.log` (10 MB, 14 días).
- Cada log de job incluye `job_id` como `extra`.
- `LOG_LEVEL` configurable. En `DEBUG` se loguean payloads (truncados a 1 KB) sin secretos.

---

## 15. Errores

Jerarquía:

```python
class KieError(Exception): ...
class KieClientError(KieError): ...        # HTTP 4xx
class KieServerError(KieError): ...        # HTTP 5xx tras retries
class KieTimeoutError(KieError): ...       # polling agotado
class JobValidationError(ValueError): ...
```

Reglas:

- `JobRunner` traduce excepciones a `job.error = str(exc)` y marca `FAILED`.
- La UI muestra `error` truncado, con botón "ver completo" que abre el log.

---

## 16. Tests

Stack: `pytest` + `pytest-asyncio` + `httpx.MockTransport`.

Cobertura mínima en fase 1:

- `domain/models`, `domain/policies` 100 %.
- `infra/db`: init, upsert, list_recent, list_by_status, get, delete.
- `infra/kie_client`: cada método con `MockTransport` (incluye 200 / 4xx / 5xx + retry).
- `app_layer/job_runner`: happy path + cada rama de fallo (validation, upload error, timeout, download error).
- `app_layer/queue_manager`: semáforo respetado, cancel, retry, drain.

Reglas:

- Cero llamadas reales a Kie en CI; siempre mockear.
- Fixtures en `tests/conftest.py`: `tmp_settings`, `mock_kie_client`, `inmemory_db`.

---

## 17. Calidad

- `ruff` (lint + format), `mypy` opcional pero recomendado.
- Pre-commit (futuro): ruff + ruff-format + pytest -q.
- Convenciones:
  - Type hints siempre.
  - `from __future__ import annotations`.
  - Async-first; nada de `requests` ni `time.sleep`.

---

## 18. Seguridad

- `.env` jamás se versiona (ya está en `.gitignore`).
- `presets/` puede ir a Git; nunca metas `voice_id` privados ahí si lo abrirás público.
- `data/jobs.db` es local; si se borra, se pierde el historial.
- En logs, los `Authorization` se redactean (`Bearer ***`).
- Validar siempre `output_path` para que no escape de `OUTPUTS_DIR` (path traversal en batch).

---

## 19. Estrategia de despliegue / portabilidad

- Único requisito: Python 3.11+ y `pip install -r requirements.txt`.
- Sin Docker para fase 1. Se puede agregar `Dockerfile` después si se necesita correr en VPS.
- `pyproject.toml` ya define `kie-avatar-studio` como entry-point; opcionalmente `pipx install .`.

---

## 20. Backlog / Roadmap

Ver `docs/ROADMAP.md`.

---

## 21. Decisiones (ADRs)

Cada decisión arquitectónica grande va en `docs/adr/NNNN-titulo.md`. Tres iniciales:

```text
0001-async-stack.md      -> por qué asyncio + httpx
0002-sqlite-aiosqlite.md -> por qué SQLite local y no Postgres
0003-textual-tui.md      -> por qué Textual y no Rich solo / curses
```

---

## 22. Checklist para "primera versión usable"

- [ ] Reorganizar a layout `domain/infra/app_layer/ui`.
- [ ] Implementar `KieClient.upload_file` real y test con `MockTransport`.
- [ ] Implementar `create_tts_task` + polling y descargar audio.
- [ ] Implementar `create_avatar_task` + polling + descargar video.
- [ ] `JobRunner` ejecuta end-to-end con un sample real.
- [ ] `QueueManager` corre 2 jobs en paralelo sin colisión en SQLite.
- [ ] Pantalla `new_job` funcional.
- [ ] Pantalla `queue` con live updates.
- [ ] Pantalla `job_detail` con logs.
- [ ] BatchLoader procesa `batch_jobs/video_001/` y deja `outputs/video_001/final.mp4`.
- [ ] README con quickstart y troubleshooting básico.
