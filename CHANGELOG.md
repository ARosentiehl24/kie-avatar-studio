# Changelog

Todas las entradas siguen el esquema de versionado descrito en
[`docs/VERSIONING.md`](docs/VERSIONING.md): **L** → MAJOR, **M** →
MINOR, **S** → PATCH.

## [1.2.0] — 2026-06-11

### Added (M)

- **Paralelismo selectivo por tipo de llamada Kie**: se agregaron límites
  independientes para audio, imagen, video, uploads y descargas. Esto permite
  subir throughput de imagen/video sin saturar el endpoint TTS.

### Fixed (S)

- **Reintentos transitorios de Kie.ai**: el cliente HTTP ahora reintenta
  errores de red/DNS, respuestas 5xx y respuestas `code: 5xx` embebidas
  en JSON antes de fallar. También aplica a descargas por streaming.
- **Flujo de producto promocional**: si falla la subida del producto, la
  pantalla vuelve a pedir la imagen del producto conservando la modelo base
  ya aprobada, evitando tener que regenerarla.

## [1.1.1] — 2026-06-08

### Added (S)

- **Modelos de imagen divididos (Base GPT + Escenas Nano Banana)**: Se implementó un flujo asimétrico de generación de imágenes de Kie.ai. Ahora se usa GPT Image 2 (`gpt-image-2-text-to-image`) exclusivamente para la generación inicial de la modelo base (método `prompt`), mientras que las demás generaciones (fondos, refits de escenas secundarias y composición de productos promocionales) continúan funcionando de manera eficiente usando Nano Banana 2 (`nano-banana-2`).

## [1.1.0] — 2026-06-08

### Added (L)

- **Subsistema de Automatización: workflows JSON declarativos end-to-end**.
  Nueva pantalla `AutomationScreen` (hotkey `F`) que escanea
  `workflows/*.json`, valida cada archivo y orquesta la ejecución
  paralela de todos sus steps:
  - Modelo de dominio: `WorkflowJob` + `WorkflowStep` + `ModelCreation`
    + `WorkflowPreSettings` + enums `StepType` (`a-roll` / `b-roll`),
    `WorkflowStatus`, `WorkflowStepStatus`, `WorkflowProgressKey` y
    `WorkflowProgressStatus` (progreso granular tipado por
    sub-componente).
  - State machine por step según su tipo:
    - **a-roll**: scene_image (opcional) + audio TTS + Avatar Pro →
      `final.mp4` con audio embebido (NO se descarga audio aparte).
    - **b-roll con `text`**: scene_image + audio TTS + Kling 2.6 i2v
      (silencioso) → `video.mp4` + `audio.mp3` separados.
    - **b-roll sin `text`**: scene_image + Kling 2.6 i2v → solo
      `video.mp4`.
  - Output por workflow:
    `outputs/<wf_id>/{base.png, workflow.json, step_NN_<slug>/…}`.
  - `WorkflowDB` con tablas `workflow_jobs` + `workflow_steps` y
    `upsert_step` granular para evitar lost updates con steps
    corriendo en paralelo.
  - `AtomicWorkflowManifestWriter`: regenera `output_dir/workflow.json`
    atómicamente en cada transición (tmp único por escritura + retry
    exponencial ante `PermissionError` para mitigar antivirus/OneDrive
    en Windows). Fallo permanente NO bloquea el workflow (se setea
    `manifest_write_failed=True` y se sigue ejecutando — la DB es la
    fuente de verdad).
  - `WorkflowStepRunner` con 3 métodos separados por tipo de step
    (CR-3.1 SRP) + `WorkflowRunner` orquestador con `asyncio.Lock` por
    `workflow_id` (serializa transiciones de steps paralelos).
  - **Dos limitadores distintos**: `_capacity_limiter` global (sub-jobs
    Kie hoja: image/audio/video) compartido entre las 4 colas, y
    `_workflows_limiter` exclusivo del workflow_queue (default
    `max_parallel_workflows=1`). Evita el deadlock que ocurriría si un
    workflow consumiera un slot global esperando a sus propios sub-jobs.
  - `CapacityLimitedExecutor`: wrapper que adquiere el limiter global
    antes de delegar al runner hoja. Permite al `WorkflowStepRunner`
    invocar los runners directos (no via queue) sin perder el límite
    compartido.
  - Validación cruzada del preset de voz al encolar (existe en
    `VoicePresetStore`) + revalidación del path local en
    `method=local` justo antes del upload (mitiga la race del archivo
    movido entre validación y ejecución).
  - Política TTS automática: `audio_language` no `None` fuerza el
    modelo turbo (`elevenlabs/text-to-speech-turbo-v2-5`, acepta
    `language_code`); `None` usa el multilingual default.
  - Nueva pantalla `WorkflowDetailScreen` con tabla de steps + status
    + progress granular por sub-componente.
  - Modal `ConfigureWorkflowScreen`: pre-llena `voice_preset` +
    `audio_language` del JSON; permite editarlos antes de encolar sin
    tocar el archivo.
  - Soporte de `voice_preset` (alias) ↔ `voice_preset_id` (atributo
    Python) para que el JSON del usuario use el nombre legible mientras
    el código interno mantiene el sufijo `_id`.
  - Schema validators que distinguen errores estructurales
    (excepciones) de warnings no bloqueantes (b-roll con
    `change_background=False`, p.ej.).
  - Restore al arrancar: workflows en estado no-terminal se marcan
    FAILED y el manifest se regenera inmediatamente para que un
    consumer externo no vea snapshot stale post-crash.
- **Aprobación humana de scene_image (modo `scene_approval_mode`)**. En
  modo `manual`, los b-roll que generan scene nueva con Nano Banana pausan
  el workflow en `awaiting_approval` esperando que el usuario apruebe /
  regenere / cancele desde el modal `SceneImageApprovalScreen` (botón
  "Revisar aprobación" + badge `⏳`). Evita gastar créditos en Kling
  animando una scene que salió mal. `auto` (default) sigue sin pausa.
- **Producto promocional en workflows** (`promote_product` +
  `include_product` + `product_prompt`). Un workflow puede promocionar UN
  producto global:
  - `pre_settings.promote_product: true` activa el flujo; al encolar, la
    UI pide elegir la foto del producto desde `inputs/` y la sube a Kie
    (TTL 24h). La imagen NO va en el JSON.
  - Cada step (a-roll o b-roll) con `include_product: true` + un
    `product_prompt` compone el producto sobre la modelo con Nano Banana 2
    (refs = `[base, producto]`). La scene resultante alimenta el render.
  - Nano Banana se invoca si `change_scene` **o** `include_product`
    (`needs_scene_generation`). Con `change_scene=false` +
    `include_product=true`, mantiene el fondo de la base y solo añade el
    producto.
  - La aprobación humana `manual` (solo b-roll) se amplía a la condición
    `change_scene OR include_product`; los a-roll con producto generan
    scene pero nunca pausan.
  - Validación cruzada: `include_product=true` exige
    `promote_product=true`. Ejemplo en `workflows/example_product_promo.json`.
- **Endpoint Kie nuevo**: `kling-3.0/video` (b-roll con sound effects opcionales).
  Implementado en `KieClient.create_image_to_video_task`. Documentado
  en `docs/API_KIE.md` §6.
- Nuevo hotkey global `F` (Automatización) + icono `🤖` en `_icons.py`.
- `Settings.workflows_dir` (default `./workflows/`) y
  `Settings.max_parallel_workflows` (default 1).
- Carpeta `workflows/` con README + ejemplo del JSON canónico.

### Added (M)

- **Generación de imágenes con Nano Banana 2 (Google) vía Kie**. Nuevo
  subsistema completo paralelo al de audio TTS:
  - `ImageJob` + `ImageJobRunner` + `ImageJobLifecycle` + cola persistente
    `ImageQueueManager` con la misma state machine que audio
    (`queued → validating → creating → polling → completed | failed | cancelled`).
  - `GeneratedImage` reusable como `image_url` del `VideoJob` (retención 14d en Kie).
  - Pantalla `Imágenes` expandida a **galería mixta uploaded + generated + cola**
    con botones `Cargar`, `Generar`, `Ver`, `Copiar URL`, `Cancelar job`,
    `Reintentar`, `Quitar`. Listener al `image_queue` refresca en vivo.
  - Nuevo modal `Generar imagen` con prompt (max 20k chars), settings
    (`aspect_ratio`, `resolution`, `output_format`) y selector múltiple de
    refs hasta 14 del catálogo combinado uploaded + generated.
  - `ImageAssetRef` DTO discriminado (`uploaded` / `generated`) + nuevo
    `ImageCatalogController` (facade fina) → `VideosController.enqueue_from_assets`
    ahora acepta cualquier tipo de imagen como input del avatar.
  - `HistoryController`, `HistoryScreen` y `QueueScreen` extendidos para
    incluir image jobs (con su propio filtro 🖼 + badge `image`).
  - Notificación del SO al completar/fallar (toast "✓ Imagen lista").
  - `_mark_creating_image_jobs_as_failed` al arrancar la app para evitar
    duplicar créditos si la app crasheó entre `createTask` y persistir
    `task_id` (mirror del patrón de audio).
  - Semáforo global de `max_parallel_jobs` ahora compartido entre las
    **tres** colas (video + audio + image) — test `test_cross_queue_parallelism.py`
    garantiza que el límite no se viola con jobs concurrentes de los tres
    tipos.
- Documentación de la API de Nano Banana 2 en `docs/API_KIE.md` §5.

### Changed (M)

- `VideosController.enqueue_from_assets(image_ref, audio_id, prompt)`:
  ahora recibe un `ImageAssetRef` discriminado en lugar de `image_id`
  plano. La resolución contra el store correcto (uploaded/generated) y
  el chequeo de TTL apropiado por kind (24h vs 14d) viven en
  `ImageCatalogController.resolve_asset()` (CR-3.7). Evita colisión de
  ids entre stores y bugs de expiración cruzada.
- Pantalla `Nuevo video` (`NewVideoFormScreen`): selector de imagen ahora
  acepta tanto `UploadedImage` como `GeneratedImage`, etiquetando cada
  opción como `[subida]` o `[generada]`. Devuelve `ImageAssetRef`
  (no `image_id`) para que `VideosController` resuelva sin asumir origen.

### Changed (S) — UI polish

- `ConfigureWorkflowScreen`: los campos del formulario ahora viven en un
  `VerticalScroll`, con título/subtítulo fijos arriba y los botones de
  acción fijos abajo. Antes, con muchos campos (preset + duración +
  aprobación + producto), los de abajo —incluidos los botones— se
  recortaban por overflow y eran inalcanzables. Además se corrigió un hueco
  grande causado por la fila del Select de aprobación que se expandía a
  `height: 1fr`, y los bloques de estado "Producto promocional" y "Próximo
  paso" se muestran como cards con borde redondeado para destacar del muro
  de hints.
- Botones secundarios (`.btn-info` / `.btn-success` / `.btn-warning`)
  rediseñados a estilo **ghost** (fondo tenue teñido + texto del color
  semántico) en vez de fills saturados. Más sobrios contra el tema
  tokyo-night; ahora solo el botón primary (lavanda sólido) y el destructive
  (rojo sólido) dominan la jerarquía. Afecta todas las pantallas de forma
  consistente.

---

## [1.0.1] — 2026-06-05

Hotfix del .exe de v1.0.0, que no arrancaba en ningún caso (ni standalone
ni instalado vía Inno Setup). Sin cambios funcionales user-visible: la
TUI corre exactamente igual en modo dev.

### Fixed (S)

- **Build de Windows .exe**: `dist/KieAvatarStudio.exe` fallaba al
  arrancar con `ImportError: attempted relative import with no known
  parent package` porque PyInstaller corría `kie_avatar_studio/__main__.py`
  como módulo top-level (`__main__`), sin paquete padre, y rompía los
  imports relativos del paquete. Se introdujo `packaging/entry.py` como
  wrapper con import absoluto y se actualizó `packaging/kie_avatar_studio.spec`
  para apuntar al wrapper (más paths absolutos derivados de `SPECPATH`
  para que la build sea independiente del CWD). También se agregó
  `collect_all('textual')` + `collect_submodules('pydantic'/'pydantic_settings')`
  porque `textual.widgets` lazy-loadea sus submódulos (`_tab_pane`, etc.)
  vía `__getattr__` y el analizador estático de PyInstaller no los veía.
  Test guardrail nuevo en `tests/test_main_entry.py`.
- **`.exe` instalado en Program Files**: `Settings.ensure_dirs()` usaba
  paths relativos al CWD (`./data`, `./logs`, ...). Al lanzar el shortcut
  generado por Inno Setup, el CWD era `C:\Program Files\Kie Avatar Studio\`
  → no-writable para usuarios sin admin → la app explotaba apenas
  intentaba crear los directorios. `config.py` ahora detecta
  `sys.frozen` y resuelve los defaults a `%LOCALAPPDATA%\KieAvatarStudio\`
  en Windows, `~/Library/Application Support/KieAvatarStudio/` en macOS,
  y `$XDG_DATA_HOME/KieAvatarStudio/` (o `~/.local/share/...`) en Linux.
  El `.env` queda en la misma raíz (resuelto vía `data_dir.parent` en
  `app.py:150`, sin cambios ahí). En modo dev (`python -m kie_avatar_studio`)
  el comportamiento NO cambia: paths siguen relativos al CWD. Tests
  en `tests/test_config.py`.

---

## [1.0.0] — 2026-06-05

Primera versión funcional completa. Las 10 pantallas del menú principal
están implementadas y operativas; pipeline end-to-end probado;
notificaciones del SO cross-platform; suite de 474 tests verdes.

### Added (M)

- **Pantalla Nuevo video** (`n`): flujo end-to-end image + script + voz
  + prompt → MP4 final.
- **Pantalla Procesar lote** (`b`): `BatchLoader` lee `batch_jobs/`
  con `script.txt` + `modelo.<ext>` (+ `prompt.txt`, `voice.txt`,
  `meta.json` opcionales). Encolado masivo válido / individual.
- **Pantalla Cola de trabajos** (`g`): vista unificada de video+audio
  jobs con acciones bulk cancel/retry.
- **Pantalla Historial** (`h`): jobs terminales unificados.
- **Pantalla Imágenes** (`i`): upload, validación, contador de saldo.
- **Pantalla Audios** (`a`): generación TTS, reproducción, copia de
  URL, presets cargables, contador de saldo.
- **Pantalla Presets** (`p`): CRUD file-based JSON para voice presets
  reusables (voice_id + 5 voice_settings + label).
- **Pantalla Configuración** (`c`): multi-perfil de API keys (CRUD,
  test, switch active) + edición de `.env`.
- **Pantalla Logs** (`l`): tail del log de la sesión.
- **Notificaciones del SO** cross-platform al terminar un job
  (`COMPLETED`/`FAILED`): Linux (`notify-send`), macOS (`osascript`),
  Windows 10+ (PowerShell + WinRT). `NOTIFICATIONS_ENABLED` en `.env`.
- **Copy-to-clipboard robusto** multi-backend: `wl-copy` / `xclip` /
  `xsel` / `pbcopy` / `clip.exe` + OSC 52 como fallback.
- **Reproductor de audio** con cadena `mpv` → `ffplay` → `mpg123` →
  fallback al launcher del SO.
- **Cola estructurada** con paralelismo limitado (`max_parallel_jobs`)
  por `asyncio.Semaphore` compartido entre video y audio queues.
- **Persistencia y restore_pending**: jobs en progreso al cerrar la
  app se reanudan al volver a abrir.
- **Sistema de colores semántico** para botones (primary/info/warning/
  error/glyph/filter) — documentado en `.github/skills/tui-designer`.
- **Validaciones de dominio** alineadas con límites duros de Kie
  (script ≤ 5000, prompt ≤ 5000, imagen ≤ 10 MB, audio ≤ 100 MB / 5 min).
- **Retención automática** de assets en Kie según TTL (24h imágenes,
  14d audios generados).

### Changed (M)

- `KieClient.__init__` degrada el warning de `KIE_API_KEY` vacío a
  `DEBUG`; el `WARNING` real solo se emite en `on_mount` si tras
  aplicar `keys.json` la key sigue vacía.
- Mensajes de "Copiar URL" simplificados a una línea (las URLs largas
  de Kie inflaban los toasts).
- Pre-commit: ruff `0.6.9` → `0.15.15`, mypy `1.11.2` → `1.13.0`.

### Fixed (S)

- CSS de `#audios-credits` / `#images-credits`: `height: 1` + `padding:
  2 4` recortaba el texto haciendo invisible el contador. Ajustado a
  `height: auto` + `padding: 0 4` + `margin-top: 1`.
- Glifos `⊘` / `↻` (que algunas fuentes renderizaban como cajas
  vacías) reemplazados por `✖` / `🔄`.
- `.gitignore`: `presets/voices/*.json` para no commitear data del
  usuario.

### Arquitectura

- 4 capas con imports en una sola dirección (CR-1):
  `ui → app_layer → domain` + `infra → domain` + `app.py` como
  composition root. Validado por `import-linter` (4 contratos KEPT).
- `domain/`: Pydantic models, errores tipados, eventos, Protocols.
- `infra/`: HTTP (httpx), SQLite (aiosqlite), file-based stores.
- `app_layer/`: controllers + queue + state machines.
- `ui/`: pantallas Textual con TCSS dedicado.

### Tests

- **474 verdes** total.
- ruff + mypy strict + import-linter en pre-commit.
- Cobertura ~75% (objetivo Fase 4: 80%).

---

[Unreleased]: https://github.com/_/_/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/_/_/releases/tag/v1.0.1
[1.0.0]: https://github.com/_/_/releases/tag/v1.0.0
