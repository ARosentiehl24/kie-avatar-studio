# Changelog

Todas las entradas siguen el esquema de versionado descrito en
[`docs/VERSIONING.md`](docs/VERSIONING.md): **L** → MAJOR, **M** → MINOR, **S**
→ PATCH.

## [Unreleased]

Sin cambios.

## [2.0.0] — 2026-06-15

### Added (M) — 2.0.0

- **Selector de voz ElevenLabs en Automatización**: el modal
  `Configurar workflow` ahora expone un acceso directo al `voice_changer` del
  workflow. Abre un selector que consulta `ElevenLabsClient.list_voices()` al
  momento de abrirse, permite elegir una voz o dejar "Sin voice changer", y
  refleja la selección en el resumen final antes de encolar. Si falta
  `ELEVENLABS_API_KEY`, el control queda deshabilitado y la UI muestra el
  mensaje de configuración correspondiente.
- **ELEVENLABS_API_KEY en Configuración**: la pantalla `Configuración` ahora
  incluye la pestaña "Integraciones" para guardar o limpiar la API key directa
  de ElevenLabs sin editar `.env` a mano.
- **Botones de alto contraste**: el sistema global de `Button` en TCSS ahora usa
  bordes visibles, texto en negrita y estados `hover`/`focus`/ `active` más
  marcados para estandarizar todas las acciones de la TUI.

### Changed (S) — 2.0.0

- **Limpieza de compat legacy en workflows**: `pre_settings.voice_preset_id` /
  `voice_preset` deja de estar soportado en runtime (loader/controller/UI). El
  flujo actual usa `pre_settings.voice_changer` para la conversión STS.
- **Integraciones en `keys.json`**: la key de ElevenLabs ahora se sincroniza
  también en `data/keys.json` bajo `integrations.elevenlabs_api_key` (además de
  `.env`) para centralizar credenciales en un solo store local.
- **Prompts sin guard visual forzado**: se removió la inyección automática de
  `guard visual` en prompts de imagen/video; la app ahora envía los
  prompts tal cual se configuran.
- **Prompts en español**: el hint runtime para preservar fondo y las cadenas
  de guard visual quedaron en español, junto con el workflow de ejemplo
  `workflows/000-SANITY-MATRIX-v2.json`, para evitar mezcla de idiomas en los
  textos de prompt.
- **Composición de prompts por escenario de producto**: en workflows, los
  steps con `include_product=true` + `include_model=false` ahora fuerzan un
  hint explícito de "solo producto" con foco en el producto y permitiendo
  interacción humana parcial (p. ej., manos), mientras que los pasos con
  modelo siguen usando el hint de preservar fondo cuando `change_scene=false`.
  Además, el prompt enviado a VEO ahora incorpora el texto hablado (`step.text`)
  cuando está presente para alinear acción visual y diálogo/narración.
- **Continuidad de base entre escenas**: se agrega `set_as_base` por step para
  promover la `scene_image` generada como nueva base de los siguientes planos.
  Cuando está presente en algún step, el runner ejecuta el workflow en serie
  para mantener orden determinista de continuidad.
- **Retry con recuperación de producto promocional**: al reintentar workflows,
  ahora se detecta si la referencia del producto expiró o ya no existe para los
  steps pendientes con `include_product=true`. Si hay `local_path` válido, se
  recarga automáticamente; si no, la UI abre un flujo para volver a elegir y
  subir el producto antes de reencolar.
- **Concat final alineado con layout real de steps**: el postproceso ahora
  busca videos en `step_<NN>_<scene_slug>/video.mp4` (layout actual), con
  fallback legacy a `<scene_slug>/video.mp4`. Esto evita falsos "sin videos
  attached" cuando sí hubo render de escenas.
- **Reintento de postproceso para workflows completados sin finales**: cuando un
  workflow quedó en `completed` pero faltan `final.mp4`/`final_audio.mp3` y sí
  existen videos de steps en disco, el botón de reintento ahora reencola el
  workflow para reconstruir solo los artefactos finales.
- **Voice changer con configuración completa en UI**: el modal de ElevenLabs
  ahora permite seleccionar voz, modelo STS, remoción de ruido y formato de
  salida antes de ejecutar el workflow. También expone `voice_settings`
  opcionales (`stability`, `similarity_boost`, `style`, `speed`) y los envía
  como JSON string al endpoint speech-to-speech.
- **Preview de voces ElevenLabs en Automatización**: el selector de
  `voice_changer` ahora permite escuchar/detener el `preview_url` de la voz
  elegida antes de encolar el workflow.
- **Recrear escena desde detalle de workflow**: la pantalla de detalle permite
  seleccionar un step completado y reencolarlo para regenerar su video,
  limpiando los finales previos para reconstruir `final.mp4`/audio con el clip
  nuevo.
- **Inyección de ElevenLabs en runtime de workflows**: `WorkflowRunner` ahora
  recibe y recarga correctamente `ElevenLabsClient`, evitando fallos
  `voice_changer configurado pero ElevenLabsClient no fue inyectado`.

### Breaking (L) — 2.0.0

- **Pipeline de video de workflows migrado a VEO 3.1**: la automatización deja
  atrás Avatar Pro / Kling 3.0 y ahora renderiza todas las escenas con
  `POST /api/v1/veo/generate` usando
  `generationType=FIRST_AND_LAST_FRAMES_2_VIDEO`.
- **TTS removido del runtime de workflows**: los workflows ya no crean audio
  ElevenLabs vía Kie por step; VEO genera audio nativo embebido en cada MP4.
- **Schema JSON v2 para workflows**: se agregan `pre_settings.veo`,
  `pre_settings.voice_changer` y el campo `attached` por step para controlar el
  reel final.
- **Campos deprecated en `pre_settings`**: `audio_language` e
  `i2v_duration_seconds` quedan aceptados solo por backward compat y emiten
  warning en el loader.

### Added (M) — 2.0.0

- **Integración con VEO 3.1**: nuevo backend de video para automatización con
  los modelos `veo3`, `veo3_fast` y `veo3_lite`, apoyado en
  `FIRST_AND_LAST_FRAMES_2_VIDEO`.
- **Cliente directo de ElevenLabs para speech-to-speech**: se incorpora
  `ElevenLabsClient` para aplicar voice changing al audio final del workflow sin
  pasar por Kie.
- **Postproceso local con FFmpeg**: nueva capa para concatenar videos, extraer
  audio y preparar el material que luego se transforma con speech-to-speech.
- **Pipeline post-workflow automático**: al terminar los steps se concatena la
  lista de escenas `attached`, se extrae `final_audio.mp3` y opcionalmente se
  genera `voice_changed_audio.mp3`.
- **Campo `attached` por step**: cada escena decide si participa o no del
  `final.mp4` concatenado, sin impedir que el clip individual se renderice y se
  descargue.
- **Selector de voces ElevenLabs en UI**: la automatización suma un selector de
  voces para `pre_settings.voice_changer` usando la API directa de ElevenLabs.
- **Nueva configuración**: `ELEVENLABS_API_KEY`, `MAX_PARALLEL_VEO_JOBS` y
  `FFMPEG_PATH`.

### Removed (S) — 2.0.0

- **Helpers legacy de video workflow**: se eliminan `render_avatar_video()` y
  `render_i2v_video()` de `workflow_kie_helpers` porque el render ahora pasa por
  VEO + `veo_poller`.
- **Step runner legacy por tipo**: desaparecen `_run_a_roll`,
  `_run_b_roll_with_audio` y `_run_b_roll_silent`; `WorkflowStepRunner` converge
  en `_run_veo()`.

## [1.4.0] — 2026-06-13

### Added (M) — 1.4.0

- **Concurrencia configurable desde la UI**: nueva pestaña "Concurrencia" en
  Configuración que expone los 5 límites por subsistema
  (`MAX_PARALLEL_AUDIO_JOBS`, `MAX_PARALLEL_IMAGE_JOBS`,
  `MAX_PARALLEL_VIDEO_JOBS`, `MAX_PARALLEL_UPLOAD_JOBS`,
  `MAX_PARALLEL_DOWNLOAD_JOBS`) que antes solo se podían tocar editando el
  `.env` a mano. Los cambios persisten en `.env` y se aplican al reiniciar la
  app (los semáforos viven en el composition root).
- **Idioma del audio como dropdown en "Generar Audio"**: el campo
  `language_code` del modal de generación de audio TTS pasa de Input libre a
  `Select` con la misma lista curada que `preset_form.py` (Auto, Español
  419/ES/es-ES, English US/UK, Português, Français, Deutsch, Italiano, Polski,
  Türkçe, हिन्दी, العربية, 中文, 日本語, 한국어). Códigos custom desconocidos se
  preservan automáticamente.
- **Menú principal agrupado por propósito**: las 11 opciones se reorganizan en 4
  secciones lógicas con headers visuales (`── CREAR ──`, `── MONITOREO ──`,
  `── BIBLIOTECA ──`, `── SISTEMA ──`) para escaneo rápido. Las opciones
  aparecen ahora en un orden fijo razonado (Crear: Automatización → Nuevo video
  → Procesar lote; Monitoreo: Cola → Historial; Biblioteca: Imágenes → Audios →
  Presets; Sistema: Configuración → Logs → Salir). Los hotkeys
  (F/N/B/G/H/I/A/P/C/L/Q) se preservan para no romper memoria muscular. Los
  headers son `Option(disabled=True)` y el `on_mount` posiciona el highlight
  inicial en la primera opción real.

### Changed (M) — 1.4.0

- **Vocabulario de botones estandarizado**: todos los botones de texto ahora son
  [b]dinámicos por contenido[/b] vía el base
  `Button { width: auto; min-width: 12; padding: 0 2 }`. Se eliminaron los
  anchos fijos legacy (`.actions-row-save Button { width: 28 }`,
  `#video-form-footer Button { width: 22 }`) y los 7 overrides ad-hoc de
  `min-width: 16` esparcidos por footers de modales y rows con Input. Las
  variantes "ghost" (`btn-info`/`btn-success`/`btn-warning`) suben de
  `background: COLOR 15%` a `30%` (base) y `50%`/`65%` (hover/active) para que
  el fondo sea claramente visible y deje de parecer transparente.
- **Familia visual unificada en 3 tiers**: todos los botones del repo ahora
  comparten un mismo lenguaje visual con `text-style: bold` consistente. Tier 1
  (solid fill — `variant="primary"`, `variant="error"`) para acciones
  principales y destructivas. Tier 2 (tinted 40/60/75 — `.btn-info`,
  `.btn-success`, `.btn-warning`) para acciones secundarias con código de color
  semántico. Tier 3 (neutral `$boost-lighten-1` — `variant="default"`) para
  cancelar/cerrar y acciones de baja prioridad (antes era transparente y se
  perdía visualmente). El helper `.btn-filter` (toggles de pestaña en
  Cola/Historial) también se alinea al patrón de hover con `$accent` tint en
  lugar de los rojos/grises sueltos previos. Se corrigieron además 3
  inconsistencias semánticas: `key-test` (Probar API key) pasa de `btn-warning`
  a `btn-info` (es una acción de lectura, no destructiva), `automation-retry`
  (Reintentar workflow) pasa de `btn-info` a `btn-warning` (re-ejecuta y vuelve
  a gastar créditos, igual que
  `queue-retry`/`aud-retry`/`vid-retry`/`img-retry`), y `summary-cancel`
  ("Volver a editar") pasa de `btn-info` a `variant="default"` (Tier 3) porque
  es semánticamente un back/dismiss.
- **Terminología del modelo de b-roll actualizada**: Kling 3.0 dejó de llamar a
  su endpoint "image-to-video" — el modelo se llama ahora `kling-3.0/video` y la
  documentación pública lo refiere como "video" / "b-roll". Como consecuencia:
  el método HTTP `KieClient.create_image_to_video_task` se renombra a
  `create_kling_video_task` (junto con el `Protocol` `KieGateway` y el wrapper
  `LimitedKieGateway`), y todas las referencias en docs (`README.md`,
  `docs/API_KIE.md`, `workflows/README.md`, `workflows/SCHEMA_REFERENCE.md`) y
  docstrings (`visual_prompt_guard`, `workflow_kie_helpers`) pasan de
  "image-to-video" / "Kling i2v" a "Kling 3.0 video" o simplemente "b-roll". El
  campo público `pre_settings.i2v_duration_seconds` del schema de workflows se
  preserva tal cual para no romper JSONs existentes (sigue siendo el override
  global de duración del b-roll). Los símbolos Python internos con prefijo `i2v`
  (constantes `DEFAULT_I2V_*`, `validate_i2v_duration`, file
  `test_kie_client_i2v.py`) también se mantienen — son abreviación, no nombre
  del modelo.

### Removed (S) — 1.4.0

- **Clase CSS `.btn-glyph` muerta**: se elimina del `styles.tcss` (3 reglas:
  base + `:hover` + `.-active`) porque ningún widget de la app la usaba
  (`grep btn-glyph` → 0 hits en `*.py`). El caso histórico (botones cuadrados
  con emoji ⏹ y 🔁) ya había migrado a labels de texto + `btn-warning` en
  releases anteriores y la clase quedó huérfana.

### Docs (S) — 1.4.0

- **README puesto al día**: se elimina la sección "Estado" que estancaba la
  versión en v1.0.0 ("10 pantallas") y se reemplaza la descripción inicial con
  los cuatro subsistemas reales del producto (Video con avatar, B-roll con Kling
  3.0, Audio TTS, Imágenes, Workflows declarativos). El dump inline de variables
  de entorno se reemplaza por un pointer a `.env.example` para eliminar el
  riesgo de drift. Se actualiza la sección de flujos: en vez de listar solo el
  state machine de video, ahora menciona que cada subsistema tiene el suyo y
  apunta a `docs/SPEC.md` y `docs/ARCHITECTURE.md`. Se suma `workflows/` a la
  lista de directorios del repo root y se documenta el limitador exclusivo
  `MAX_PARALLEL_WORKFLOWS`.

## [1.3.1] — 2026-06-13

### Fixed (S) — 1.3.1

- **Subtítulos en chino e iconos en videos Avatar Pro / Kling i2v**: el guard
  visual se separó en dos políticas distintas según destino. Generación de
  imagen (Nano Banana 2, GPT Image 2) usa `IMAGE_VISUAL_GUARD` con política
  preventiva ("NO incluir"). Generación de video (Kling AI Avatar Pro, Kling
  i2v) usa `VIDEO_VISUAL_GUARD` con política eliminativa ("REMOVER si aparece,
  no preservar"). Ambos guards listan ahora explícitamente caracteres CJK
  (chino/japonés/coreano), UI de apps sociales
  (TikTok/Douyin/Instagram/WhatsApp), notification badges, brand logos y
  watermarks. El guard anterior, al pedir "preservar texto naturalmente
  presente", instruía a Avatar Pro a mantener intactas las alucinaciones de
  texto que Nano Banana hubiese inyectado en la `scene_image`.

## [1.3.0] — 2026-06-12

### Added (M) — 1.3.0

- **Audio separado para a-roll**: los steps `a-roll` ahora descargan también
  `audio.mp3` junto a `final.mp4`, igual que los b-roll con voiceover.
- **Limpieza segura de estado local**: se agrega script y opción en
  Configuración para limpiar la DB runtime (`jobs.db*`) conservando API keys,
  outputs, inputs, presets y workflows.

### Fixed (S) — 1.3.0

- **Guard anti-letreros en prompts visuales**: se añade una política global anti
  text overlays/captions/signage a prompts de modelo base, escenas, Avatar Pro y
  Kling i2v para reducir texto inventado en a-roll y b-roll.

## [1.2.2] — 2026-06-12

### Changed (S) — 1.2.2

- **Selector de idioma en presets de voz**: el campo avanzado `language_code`
  ahora es un dropdown con códigos BCP-47 comunes aceptados por Kie/ElevenLabs
  (`es`, `es-419`, `es-ES`, `en`, `pt-BR`, etc.), evitando que el usuario tenga
  que recordar el formato correcto.

## [1.2.1] — 2026-06-11

### Fixed (S) — 1.2.1

- **Validación de API keys**: la pantalla Configuración ahora valida keys contra
  `/api/v1/chat/credit`, que confirma autenticación y saldo. Se deja de usar
  `recordInfo` con un task inexistente porque Kie puede responder
  `code:422 recordInfo is null` aunque la key sea válida.

## [1.2.0] — 2026-06-11

### Added (M) — 1.2.0

- **Paralelismo selectivo por tipo de llamada Kie**: se agregaron límites
  independientes para audio, imagen, video, uploads y descargas. Esto permite
  subir throughput de imagen/video sin saturar el endpoint TTS.

### Fixed (S) — 1.2.0

- **Reintentos transitorios de Kie.ai**: el cliente HTTP ahora reintenta errores
  de red/DNS, respuestas 5xx y respuestas `code: 5xx` embebidas en JSON antes de
  fallar. También aplica a descargas por streaming.
- **Flujo de producto promocional**: si falla la subida del producto, la
  pantalla vuelve a pedir la imagen del producto conservando la modelo base ya
  aprobada, evitando tener que regenerarla.

## [1.1.1] — 2026-06-08

### Added (S) — 1.1.1

- **Modelos de imagen divididos (Base GPT + Escenas Nano Banana)**: Se
  implementó un flujo asimétrico de generación de imágenes de Kie.ai. Ahora se
  usa GPT Image 2 (`gpt-image-2-text-to-image`) exclusivamente para la
  generación inicial de la modelo base (método `prompt`), mientras que las demás
  generaciones (fondos, refits de escenas secundarias y composición de productos
  promocionales) continúan funcionando de manera eficiente usando Nano Banana 2
  (`nano-banana-2`).

## [1.1.0] — 2026-06-08

### Added (L) — 1.1.0

- **Subsistema de Automatización: workflows JSON declarativos end-to-end**.
  Nueva pantalla `AutomationScreen` (hotkey `F`) que escanea `workflows/*.json`,
  valida cada archivo y orquesta la ejecución paralela de todos sus steps:
  - Modelo de dominio: `WorkflowJob` + `WorkflowStep` + `ModelCreation`
    - `WorkflowPreSettings` + enums `StepType` (`a-roll` / `b-roll`),
      `WorkflowStatus`, `WorkflowStepStatus`, `WorkflowProgressKey` y
      `WorkflowProgressStatus` (progreso granular tipado por sub-componente).
  - State machine por step según su tipo:
    - **a-roll**: scene_image (opcional) + audio TTS + Avatar Pro → `final.mp4`
      con audio embebido (NO se descarga audio aparte).
    - **b-roll con `text`**: scene_image + audio TTS + Kling 2.6 i2v
      (silencioso) → `video.mp4` + `audio.mp3` separados.
    - **b-roll sin `text`**: scene_image + Kling 2.6 i2v → solo `video.mp4`.
  - Output por workflow:
    `outputs/<wf_id>/{base.png, workflow.json, step_NN_<slug>/…}`.
  - `WorkflowDB` con tablas `workflow_jobs` + `workflow_steps` y `upsert_step`
    granular para evitar lost updates con steps corriendo en paralelo.
  - `AtomicWorkflowManifestWriter`: regenera `output_dir/workflow.json`
    atómicamente en cada transición (tmp único por escritura + retry exponencial
    ante `PermissionError` para mitigar antivirus/OneDrive en Windows). Fallo
    permanente NO bloquea el workflow (se setea `manifest_write_failed=True` y
    se sigue ejecutando — la DB es la fuente de verdad).
  - `WorkflowStepRunner` con 3 métodos separados por tipo de step (CR-3.1 SRP) +
    `WorkflowRunner` orquestador con `asyncio.Lock` por `workflow_id` (serializa
    transiciones de steps paralelos).
  - **Dos limitadores distintos**: `_capacity_limiter` global (sub-jobs Kie
    hoja: image/audio/video) compartido entre las 4 colas, y
    `_workflows_limiter` exclusivo del workflow_queue (default
    `max_parallel_workflows=1`). Evita el deadlock que ocurriría si un workflow
    consumiera un slot global esperando a sus propios sub-jobs.
  - `CapacityLimitedExecutor`: wrapper que adquiere el limiter global antes de
    delegar al runner hoja. Permite al `WorkflowStepRunner` invocar los runners
    directos (no via queue) sin perder el límite compartido.
  - Validación cruzada del preset de voz al encolar (existe en
    `VoicePresetStore`) + revalidación del path local en `method=local` justo
    antes del upload (mitiga la race del archivo movido entre validación y
    ejecución).
  - Política TTS automática: `audio_language` no `None` fuerza el modelo turbo
    (`elevenlabs/text-to-speech-turbo-v2-5`, acepta `language_code`); `None` usa
    el multilingual default.
  - Nueva pantalla `WorkflowDetailScreen` con tabla de steps + status
    - progress granular por sub-componente.
  - Modal `ConfigureWorkflowScreen`: pre-llena `voice_preset` + `audio_language`
    del JSON; permite editarlos antes de encolar sin tocar el archivo.
  - Soporte de `voice_preset` (alias) ↔ `voice_preset_id` (atributo Python) para
    que el JSON del usuario use el nombre legible mientras el código interno
    mantiene el sufijo `_id`.
  - Schema validators que distinguen errores estructurales (excepciones) de
    warnings no bloqueantes (b-roll con `change_background=False`, p.ej.).
  - Restore al arrancar: workflows en estado no-terminal se marcan FAILED y el
    manifest se regenera inmediatamente para que un consumer externo no vea
    snapshot stale post-crash.
- **Aprobación humana de scene_image (modo `scene_approval_mode`)**. En modo
  `manual`, los b-roll que generan scene nueva con Nano Banana pausan el
  workflow en `awaiting_approval` esperando que el usuario apruebe / regenere /
  cancele desde el modal `SceneImageApprovalScreen` (botón "Revisar
  aprobación" + badge `⏳`). Evita gastar créditos en Kling animando una scene
  que salió mal. `auto` (default) sigue sin pausa.
- **Producto promocional en workflows** (`promote_product` + `include_product` +
  `product_prompt`). Un workflow puede promocionar UN producto global:
  - `pre_settings.promote_product: true` activa el flujo; al encolar, la UI pide
    elegir la foto del producto desde `inputs/` y la sube a Kie (TTL 24h). La
    imagen NO va en el JSON.
  - Cada step (a-roll o b-roll) con `include_product: true` + un
    `product_prompt` compone el producto sobre la modelo con Nano Banana 2 (refs
    = `[base, producto]`). La scene resultante alimenta el render.
  - Nano Banana se invoca si `change_scene` **o** `include_product`
    (`needs_scene_generation`). Con `change_scene=false` +
    `include_product=true`, mantiene el fondo de la base y solo añade el
    producto.
  - La aprobación humana `manual` (solo b-roll) se amplía a la condición
    `change_scene OR include_product`; los a-roll con producto generan scene
    pero nunca pausan.
  - Validación cruzada: `include_product=true` exige `promote_product=true`.
    Ejemplo en `workflows/example_product_promo.json`.
- **Endpoint Kie nuevo**: `kling-3.0/video` (b-roll con sound effects
  opcionales). Implementado en `KieClient.create_kling_video_task` (renombrado
  en v1.4.0). Documentado en `docs/API_KIE.md` §6.
- Nuevo hotkey global `F` (Automatización) + icono `🤖` en `_icons.py`.
- `Settings.workflows_dir` (default `./workflows/`) y
  `Settings.max_parallel_workflows` (default 1).
- Carpeta `workflows/` con README + ejemplo del JSON canónico.

### Added (M) — 1.1.0 (imágenes)

- **Generación de imágenes con Nano Banana 2 (Google) vía Kie**. Nuevo
  subsistema completo paralelo al de audio TTS:
  - `ImageJob` + `ImageJobRunner` + `ImageJobLifecycle` + cola persistente
    `ImageQueueManager` con la misma state machine que audio
    (`queued → validating → creating → polling → completed | failed | cancelled`).
  - `GeneratedImage` reusable como `image_url` del `VideoJob` (retención 14d en
    Kie).
  - Pantalla `Imágenes` expandida a **galería mixta uploaded + generated +
    cola** con botones `Cargar`, `Generar`, `Ver`, `Copiar URL`, `Cancelar job`,
    `Reintentar`, `Quitar`. Listener al `image_queue` refresca en vivo.
  - Nuevo modal `Generar imagen` con prompt (max 20k chars), settings
    (`aspect_ratio`, `resolution`, `output_format`) y selector múltiple de refs
    hasta 14 del catálogo combinado uploaded + generated.
  - `ImageAssetRef` DTO discriminado (`uploaded` / `generated`) + nuevo
    `ImageCatalogController` (facade fina) →
    `VideosController.enqueue_from_assets` ahora acepta cualquier tipo de imagen
    como input del avatar.
  - `HistoryController`, `HistoryScreen` y `QueueScreen` extendidos para incluir
    image jobs (con su propio filtro 🖼 + badge `image`).
  - Notificación del SO al completar/fallar (toast "✓ Imagen lista").
  - `_mark_creating_image_jobs_as_failed` al arrancar la app para evitar
    duplicar créditos si la app crasheó entre `createTask` y persistir `task_id`
    (mirror del patrón de audio).
  - Semáforo global de `max_parallel_jobs` ahora compartido entre las **tres**
    colas (video + audio + image) — test `test_cross_queue_parallelism.py`
    garantiza que el límite no se viola con jobs concurrentes de los tres tipos.
- Documentación de la API de Nano Banana 2 en `docs/API_KIE.md` §5.

### Changed (M) — 1.1.0 (imágenes)

- `VideosController.enqueue_from_assets(image_ref, audio_id, prompt)`: ahora
  recibe un `ImageAssetRef` discriminado en lugar de `image_id` plano. La
  resolución contra el store correcto (uploaded/generated) y el chequeo de TTL
  apropiado por kind (24h vs 14d) viven en
  `ImageCatalogController.resolve_asset()` (CR-3.7). Evita colisión de ids entre
  stores y bugs de expiración cruzada.
- Pantalla `Nuevo video` (`NewVideoFormScreen`): selector de imagen ahora acepta
  tanto `UploadedImage` como `GeneratedImage`, etiquetando cada opción como
  `[subida]` o `[generada]`. Devuelve `ImageAssetRef` (no `image_id`) para que
  `VideosController` resuelva sin asumir origen.

### Changed (S) — UI polish

- `ConfigureWorkflowScreen`: los campos del formulario ahora viven en un
  `VerticalScroll`, con título/subtítulo fijos arriba y los botones de acción
  fijos abajo. Antes, con muchos campos (preset + duración + aprobación +
  producto), los de abajo —incluidos los botones— se recortaban por overflow y
  eran inalcanzables. Además se corrigió un hueco grande causado por la fila del
  Select de aprobación que se expandía a `height: 1fr`, y los bloques de estado
  "Producto promocional" y "Próximo paso" se muestran como cards con borde
  redondeado para destacar del muro de hints.
- Botones secundarios (`.btn-info` / `.btn-success` / `.btn-warning`)
  rediseñados a estilo **ghost** (fondo tenue teñido + texto del color
  semántico) en vez de fills saturados. Más sobrios contra el tema tokyo-night;
  ahora solo el botón primary (lavanda sólido) y el destructive (rojo sólido)
  dominan la jerarquía. Afecta todas las pantallas de forma consistente.

---

## [1.0.1] — 2026-06-05

Hotfix del .exe de v1.0.0, que no arrancaba en ningún caso (ni standalone ni
instalado vía Inno Setup). Sin cambios funcionales user-visible: la TUI corre
exactamente igual en modo dev.

### Fixed (S) — 1.0.1

- **Build de Windows .exe**: `dist/KieAvatarStudio.exe` fallaba al arrancar con
  `ImportError: attempted relative import with no known parent package` porque
  PyInstaller corría `kie_avatar_studio/__main__.py` como módulo top-level
  (`__main__`), sin paquete padre, y rompía los imports relativos del paquete.
  Se introdujo `packaging/entry.py` como wrapper con import absoluto y se
  actualizó `packaging/kie_avatar_studio.spec` para apuntar al wrapper (más
  paths absolutos derivados de `SPECPATH` para que la build sea independiente
  del CWD). También se agregó `collect_all('textual')` +
  `collect_submodules('pydantic'/'pydantic_settings')` porque `textual.widgets`
  lazy-loadea sus submódulos (`_tab_pane`, etc.) vía `__getattr__` y el
  analizador estático de PyInstaller no los veía. Test guardrail nuevo en
  `tests/test_main_entry.py`.
- **`.exe` instalado en Program Files**: `Settings.ensure_dirs()` usaba paths
  relativos al CWD (`./data`, `./logs`, ...). Al lanzar el shortcut generado por
  Inno Setup, el CWD era `C:\Program Files\Kie Avatar Studio\` → no-writable
  para usuarios sin admin → la app explotaba apenas intentaba crear los
  directorios. `config.py` ahora detecta `sys.frozen` y resuelve los defaults a
  `%LOCALAPPDATA%\KieAvatarStudio\` en Windows,
  `~/Library/Application Support/KieAvatarStudio/` en macOS, y
  `$XDG_DATA_HOME/KieAvatarStudio/` (o `~/.local/share/...`) en Linux. El `.env`
  queda en la misma raíz (resuelto vía `data_dir.parent` en `app.py:150`, sin
  cambios ahí). En modo dev (`python -m kie_avatar_studio`) el comportamiento NO
  cambia: paths siguen relativos al CWD. Tests en `tests/test_config.py`.

---

## [1.0.0] — 2026-06-05

Primera versión funcional completa. Las 10 pantallas del menú principal están
implementadas y operativas; pipeline end-to-end probado; notificaciones del SO
cross-platform; suite de 474 tests verdes.

### Added (M) — 1.0.0

- **Pantalla Nuevo video** (`n`): flujo end-to-end image + script + voz
  - prompt → MP4 final.
- **Pantalla Procesar lote** (`b`): `BatchLoader` lee `batch_jobs/` con
  `script.txt` + `modelo.<ext>` (+ `prompt.txt`, `voice.txt`, `meta.json`
  opcionales). Encolado masivo válido / individual.
- **Pantalla Cola de trabajos** (`g`): vista unificada de video+audio jobs con
  acciones bulk cancel/retry.
- **Pantalla Historial** (`h`): jobs terminales unificados.
- **Pantalla Imágenes** (`i`): upload, validación, contador de saldo.
- **Pantalla Audios** (`a`): generación TTS, reproducción, copia de URL, presets
  cargables, contador de saldo.
- **Pantalla Presets** (`p`): CRUD file-based JSON para voice presets reusables
  (voice_id + 5 voice_settings + label).
- **Pantalla Configuración** (`c`): multi-perfil de API keys (CRUD, test, switch
  active) + edición de `.env`.
- **Pantalla Logs** (`l`): tail del log de la sesión.
- **Notificaciones del SO** cross-platform al terminar un job
  (`COMPLETED`/`FAILED`): Linux (`notify-send`), macOS (`osascript`), Windows
  10+ (PowerShell + WinRT). `NOTIFICATIONS_ENABLED` en `.env`.
- **Copy-to-clipboard robusto** multi-backend: `wl-copy` / `xclip` / `xsel` /
  `pbcopy` / `clip.exe` + OSC 52 como fallback.
- **Reproductor de audio** con cadena `mpv` → `ffplay` → `mpg123` → fallback al
  launcher del SO.
- **Cola estructurada** con paralelismo limitado (`max_parallel_jobs`) por
  `asyncio.Semaphore` compartido entre video y audio queues.
- **Persistencia y restore_pending**: jobs en progreso al cerrar la app se
  reanudan al volver a abrir.
- **Sistema de colores semántico** para botones (primary/info/warning/
  error/glyph/filter) — documentado en `.github/skills/tui-designer`.
- **Validaciones de dominio** alineadas con límites duros de Kie (script ≤ 5000,
  prompt ≤ 5000, imagen ≤ 10 MB, audio ≤ 100 MB / 5 min).
- **Retención automática** de assets en Kie según TTL (24h imágenes, 14d audios
  generados).

### Changed (M) — 1.0.0

- `KieClient.__init__` degrada el warning de `KIE_API_KEY` vacío a `DEBUG`; el
  `WARNING` real solo se emite en `on_mount` si tras aplicar `keys.json` la key
  sigue vacía.
- Mensajes de "Copiar URL" simplificados a una línea (las URLs largas de Kie
  inflaban los toasts).
- Pre-commit: ruff `0.6.9` → `0.15.15`, mypy `1.11.2` → `1.13.0`.

### Fixed (S) — 1.0.0

- CSS de `#audios-credits` / `#images-credits`: `height: 1` + `padding: 2 4`
  recortaba el texto haciendo invisible el contador. Ajustado a `height: auto` +
  `padding: 0 4` + `margin-top: 1`.
- Glifos `⊘` / `↻` (que algunas fuentes renderizaban como cajas vacías)
  reemplazados por `✖` / `🔄`.
- `.gitignore`: `presets/voices/*.json` para no commitear data del usuario.

### Arquitectura

- 4 capas con imports en una sola dirección (CR-1): `ui → app_layer → domain` +
  `infra → domain` + `app.py` como composition root. Validado por
  `import-linter` (4 contratos KEPT).
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
