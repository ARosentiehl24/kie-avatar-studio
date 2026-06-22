<!-- markdownlint-disable MD013 -->
# Auditoría de Flujos — Junio 2026 (v2: flujo VEO 3.1 confirmado)

> **Documento de revisión post-v1.4.0.** Captura el estado real al 2026-06-15,
> las inconsistencias detectadas en el flujo actual, y el diseño del flujo nuevo
> end-to-end basado en **VEO 3.1 vía Kie** (decisión confirmada por la usuaria)
> con **eliminación de Kling y TTS automatizado del workflow**.
>
> NO es spec definitiva — es input para el ADR + spec del próximo release
> (v2.0.0 — mayor, breaking change). Cuando se implemente, mover las decisiones
> aprobadas a `docs/SPEC.md` y archivar este doc.

---

## 1. Decisiones tomadas

| #   | Decisión                                                                                                         | Confirmado por                                                                                                 |
| --- | ---------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| 1   | **VEO 3.1 reemplaza a Kling como motor de video del workflow nuevo**                                             | Usuaria (2026-06-15)                                                                                           |
| 2   | **TTS ElevenLabs vía Kie SE ELIMINA del workflow nuevo** (VEO genera audio nativo embebido en el MP4)            | Usuaria                                                                                                        |
| 3   | **Avatar Pro (`kling/ai-avatar-pro`) se deprecará del workflow nuevo**                                           | Usuaria (implícito en "ya no usaremos kling")                                                                  |
| 4   | **Kling 3.0 b-roll (`kling-3.0/video`) se deprecará del workflow nuevo**                                         | Usuaria (implícito)                                                                                            |
| 5   | **Voice changer de ElevenLabs es post-procesamiento manual** (fuera de la app, o futuro pero NO en este release) | Inferencia de la usuaria — "exportar audio del video VEO + voice changer en elevenlabs" como pasos 4-5 humanos |

### Implicancia arquitectónica

El workflow actual tiene cuatro subsistemas: video con avatar (Avatar Pro),
b-roll (Kling 3.0), audio TTS (ElevenLabs) e imágenes (Nano Banana / GPT Image).
El workflow nuevo colapsa **video con avatar + b-roll + audio TTS** en un solo
paso de **VEO 3.1**, que genera un MP4 con audio embebido.

Esto NO elimina los subsistemas standalone (la pantalla "Generar Audio",
"Generar Imagen", etc. siguen existiendo como tooling). Solo el **workflow
declarativo JSON** cambia de motor.

---

## 2. API de VEO 3.1 (Kie.ai) — referencia técnica

### 2.1 Endpoints

| Método | URL                                                     | Para qué                                                             |
| ------ | ------------------------------------------------------- | -------------------------------------------------------------------- |
| `POST` | `https://api.kie.ai/api/v1/veo/generate`                | Crear task de generación                                             |
| `GET`  | `https://api.kie.ai/api/v1/veo/record-info?taskId=<id>` | Polling de status + result URLs                                      |
| `POST` | (callback opcional)                                     | Webhook con result push (timeout 15s, NO usaremos por ser app local) |

**Importante**: estos endpoints **NO siguen el patrón** `/jobs/createTask` +
`/jobs/recordInfo` que usa el resto del catálogo (Kling, Nano Banana,
ElevenLabs). VEO tiene su propio par de endpoints `/veo/*`. Eso obliga a tratar
VEO como un caso aparte en `KieClient` (no se puede reusar el helper genérico
`_create_task` + `poll_task_for_url`).

### 2.2 Modelos disponibles

| Id          | Descripción                          | Soporta `FIRST_AND_LAST_FRAMES_2_VIDEO` | Soporta `REFERENCE_2_VIDEO` | Créditos 720p / 1080p / 4K |
| ----------- | ------------------------------------ | --------------------------------------- | --------------------------- | -------------------------- |
| `veo3`      | Quality (flagship, highest fidelity) | ✅                                      | ❌                          | 250 / 255 / 370            |
| `veo3_fast` | **Default**, cost-efficient          | ✅                                      | ✅                          | **60** / 65 / 180          |
| `veo3_lite` | Most cost-effective (high volume)    | ✅                                      | ✅                          | 30 / 35 / 150              |

> **Los 60 créditos que mencionaste = veo3_fast @ 720p.**
>
> Con FIRST_AND_LAST_FRAMES_2_VIDEO **podés subir a `veo3` Quality** si querés
> más calidad de animación (a 250 créditos / 720p). Decisión a tomar en Open Q2.

### 2.3 Generation modes

| Mode                                                   | Descripción                                                                                                                                                                                                                                                                            | imageUrls |
| ------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------- |
| `TEXT_2_VIDEO`                                         | Solo prompt                                                                                                                                                                                                                                                                            | (none)    |
| `FIRST_AND_LAST_FRAMES_2_VIDEO` ✅ **modo que usamos** | **1 imagen** = se usa como first frame y VEO expande dinámicamente desde ahí. **2 imágenes** = primera como first frame, segunda como last frame → transición. Soporta los 3 modelos y duraciones 4/6/8.                                                                               | 1 o 2     |
| `REFERENCE_2_VIDEO`                                    | Material-to-video con hasta 3 imágenes de referencia (estilo + sujeto + escena). Solo en `veo3_fast` y `veo3_lite`. Duration obligado a 8s. **Lo descartamos** porque la semántica es "usa estas imágenes como referencia conceptual", no "esta es la modelo exacta del primer frame". | 1 a 3     |

Si no se especifica `generationType`, Kie lo infiere de si hay `imageUrls` o no.
**Recomendación: especificar siempre** para evitar comportamientos implícitos.

**Por qué FIRST_AND_LAST_FRAMES_2_VIDEO y no REFERENCE_2_VIDEO**:

- La modelo base generada con ChatGPT Image es el **first frame literal** del
  video, no una "referencia de estilo". El usuario quiere que la modelo del
  prompt se vea **idéntica** a la imagen, no inspirada por ella.
- Da acceso a `veo3` Quality (REFERENCE no lo permite).
- Permite duraciones de 4 y 6s (REFERENCE forzaría 8s siempre).
- Si querés transición intro→outro (modelo → modelo con producto, por ejemplo),
  pasás 2 imágenes y VEO interpola.

### Implicancia para el flujo de la usuaria

El flujo end-to-end queda:

```text
1. ChatGPT Image (gpt-image-2-text-to-image) → modelo base (imagen estática)
2. (opcional) Nano Banana 2 → composición "modelo sosteniendo producto"
3. VEO 3.1 FIRST_AND_LAST_FRAMES_2_VIDEO con esa imagen como first frame
   → MP4 8s 9:16 720p con audio nativo
4-6. Edición / extract audio / voice changer (fuera de la app)
```

**Decisión a confirmar (Open Q1)**: ¿paso 2 con Nano Banana se mantiene? Con
`FIRST_AND_LAST_FRAMES_2_VIDEO` solo podés pasar 1 imagen como first frame — si
querés modelo+producto en el frame, **tenés que pre-componer con Nano Banana
primero** (no hay forma de pasar dos imágenes separadas en este modo, eso era lo
que ofrecía `REFERENCE_2_VIDEO`).

### 2.4 Otros parámetros del body

| Campo               | Tipo   | Default                                                       | Notas                                                                                                                                                                                                                  |
| ------------------- | ------ | ------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `prompt`            | string | —                                                             | **Required**. Soporta multilingual nativo.                                                                                                                                                                             |
| `imageUrls`         | array  | `[]`                                                          | **1-2 URLs** para FIRST_AND_LAST_FRAMES_2_VIDEO (1 = first frame solo / 2 = first+last). 1-3 URLs para REFERENCE_2_VIDEO. URLs públicas accesibles a Kie (las que devuelve `upload_file` o `kie_url` del store local). |
| `model`             | enum   | `veo3_fast`                                                   | Ver §2.2                                                                                                                                                                                                               |
| `generationType`    | enum   | (auto)                                                        | Ver §2.3 — **siempre setear explícitamente**.                                                                                                                                                                          |
| `aspect_ratio`      | enum   | `16:9`                                                        | `16:9` / `9:16` / `Auto` (Auto = center-crop según input). **Para vertical: `9:16`.**                                                                                                                                  |
| `resolution`        | enum   | `720p`                                                        | `720p` / `1080p` / `4k` (4k requiere endpoint separado + 2× créditos).                                                                                                                                                 |
| `duration`          | int    | `8`                                                           | `4` / `6` / `8`. Configurable en FIRST_AND_LAST_FRAMES_2_VIDEO. REFERENCE_2_VIDEO solo `8`.                                                                                                                            |
| `enableTranslation` | bool   | `false` en docs schema, `true` en texto descriptivo (ambiguo) | Si `true` traduce el prompt a inglés antes de generar (mejor calidad). **Recomendación: setear explícito `true`.**                                                                                                     |
| `enableFallback`    | bool   | `false`                                                       | **Deprecated.** Borrar del payload.                                                                                                                                                                                    |
| `watermark`         | string | (none)                                                        | Texto de marca de agua opcional.                                                                                                                                                                                       |
| `callBackUrl`       | string | (none)                                                        | Para webhook push. **No usaremos** — la app es local sin endpoint público.                                                                                                                                             |

### 2.5 Audio nativo

> **Confirmado por docs.kie.ai**: _"All videos ship with background audio by
> default. In rare cases, upstream may suppress audio when the scene is deemed
> sensitive (e.g. minors)."_

Esto valida que **NO necesitamos generar TTS separado** — el MP4 que descarga la
app ya viene con audio sincronizado (música/SFX/voz según el prompt). El paso 5
humano (voice changer) consume el audio del MP4 con FFmpeg local.

### 2.6 Response del POST `/veo/generate`

```json
{
  "code": 200,
  "msg": "success",
  "data": { "taskId": "veo_task_abcdef123456" }
}
```

### 2.7 Response del GET `/veo/record-info`

```json
{
  "code": 200,
  "msg": "success",
  "data": {
    "taskId": "veo_task_abcdef123456",
    "paramJson": "{...}",
    "completeTime": 1234567890,
    "successFlag": 1,
    "errorCode": null,
    "response": {
      "taskId": "veo_task_abcdef123456",
      "resultUrls": ["http://example.com/video1.mp4"],
      "originUrls": ["http://example.com/original_video1.mp4"],
      "resolution": "1080p"
    }
  }
}
```

`successFlag`:

- `0` = generando (seguir poleando)
- `1` = éxito (descargar `response.resultUrls[0]`)
- `2` = task falló pre-generación
- `3` = task creada OK pero generación upstream falló

**Retención**: solo 14 días — la app debe descargar el MP4 a `outputs/` antes de
ese plazo o perdemos el video.

### 2.8 Status codes

| Code | Significado                             | Acción runner                                                                     |
| ---- | --------------------------------------- | --------------------------------------------------------------------------------- |
| 200  | OK                                      | Continuar                                                                         |
| 400  | Content policy / 1080p still processing | Si "1080P is processing" → reintentar polling. Si content policy → FAIL terminal. |
| 401  | Unauthorized                            | FAIL (API key inválida o falta)                                                   |
| 402  | Insufficient credits                    | FAIL terminal con mensaje claro al usuario                                        |
| 404  | Not found                               | FAIL                                                                              |
| 422  | Validation error (params malos)         | FAIL terminal                                                                     |
| 429  | Rate limited                            | Backoff + retry                                                                   |
| 451  | Failed to fetch image (URL muerta)      | FAIL — revalidar refs                                                             |
| 455  | Service unavailable (mantenimiento Kie) | Retry con backoff                                                                 |
| 500  | Server error                            | Retry con backoff                                                                 |
| 501  | Generation failed (upstream)            | FAIL terminal                                                                     |
| 505  | Feature disabled                        | FAIL                                                                              |

---

## 3. Cambios necesarios en el código

### 3.1 Nuevo en `KieClient`

```python
DEFAULT_VEO_MODEL: Final[str] = "veo3_fast"
DEFAULT_VEO_DURATION_SECONDS: Final[int] = 8
DEFAULT_VEO_ASPECT_RATIO: Final[str] = "9:16"
DEFAULT_VEO_RESOLUTION: Final[str] = "720p"

async def create_veo_video_task(
    self,
    prompt: str,
    *,
    image_urls: list[str] | None = None,
    model: str = DEFAULT_VEO_MODEL,
    generation_type: str | None = None,
    aspect_ratio: str = DEFAULT_VEO_ASPECT_RATIO,
    resolution: str = DEFAULT_VEO_RESOLUTION,
    duration: int = DEFAULT_VEO_DURATION_SECONDS,
    enable_translation: bool = True,
    watermark: str | None = None,
) -> KieTaskCreated:
    """POST /api/v1/veo/generate — Veo 3.1 video con audio nativo embebido."""
```

> **No reusar `_create_task`**: VEO usa endpoint distinto (`/veo/generate`, no
> `/jobs/createTask`). Hay que escribir un POST directo.

```python
async def get_veo_task_detail(self, task_id: str) -> dict[str, Any]:
    """GET /api/v1/veo/record-info?taskId=<id>."""
```

> **No reusar `get_task_detail`**: shape de response distinto (`successFlag` en
> vez de `state`, `response.resultUrls` en vez de `recordInfo.url`).

### 3.2 Nuevo helper de polling

`app_layer/polling.py` tiene `poll_task_for_url` genérico. VEO requiere uno
propio (`poll_veo_task_for_url`) que entienda `successFlag` y mapee errores
específicos de VEO.

### 3.3 Nuevo `StepType.VEO_VIDEO`

En `domain/models.py`:

- Añadir `StepType.VEO_VIDEO = "veo-video"`.
- **Deprecar** `StepType.A_ROLL` y `StepType.B_ROLL` (mantener temporalmente
  para no romper workflows existentes; emitir warning de deprecation).

### 3.4 Nuevo branch en `WorkflowStepRunner`

`_dispatch_path` debe rutear `StepType.VEO_VIDEO` → `_run_veo`:

```python
async def _run_veo(
    self, step: WorkflowStep, context: WorkflowExecutionContext,
    on_transition: StepTransition,
) -> None:
    """Path nuevo: resuelve refs → POST /veo/generate → polling → descarga MP4."""
```

Sin paso TTS, sin paso b-roll silencioso. El MP4 final ya viene con audio.

### 3.5 Schema JSON propuesto

```jsonc
{
  "name": "Reel vertical 9:16 con VEO",
  "pre_settings": {
    "model_creation": { "method": "prompt", "prompt": "..." },
    "veo": {
      "model": "veo3_fast",
      "aspect_ratio": "9:16",
      "resolution": "720p",
      "duration": 8,
      "enable_translation": true,
    },
  },
  "steps": [
    {
      "step": 1,
      "type": "veo-video",
      "prompt": "...",
      "generation_type": "FIRST_AND_LAST_FRAMES_2_VIDEO",
      "first_frame": "base",
      "last_frame": null,
    },
  ],
}
```

`first_frame` / `last_frame` admiten tokens semánticos (`base`, `product`,
`scene_image`) o ids de assets del catálogo; el runner los resuelve a URLs antes
de llamar a VEO. Si solo se pasa `first_frame`, VEO expande dinámicamente desde
esa imagen (modo 1-imagen). Si se pasan ambos, hace transición.
`last_frame: null` es legal y equivale a no mandarlo.

### 3.6 Composition root (`app.py`)

- Nuevo limitador: `_veo_limiter = Semaphore(max_parallel_veo_jobs)`. Default
  razonable: **1** (veo3_fast = 60 créditos, no spamear).
- Nueva env var en `.env.example`: `MAX_PARALLEL_VEO_JOBS=1`.
- Wirear el nuevo path en `LimitedKieGateway` (semáforo VEO antes de delegar al
  inner client).

### 3.7 Validaciones en `domain/policies.py`

```python
VEO_MODELS = {"veo3", "veo3_fast", "veo3_lite"}
VEO_GENERATION_TYPES = {"TEXT_2_VIDEO", "FIRST_AND_LAST_FRAMES_2_VIDEO", "REFERENCE_2_VIDEO"}
VEO_ASPECT_RATIOS = {"16:9", "9:16", "Auto"}
VEO_RESOLUTIONS = {"720p", "1080p", "4k"}
VEO_DURATIONS = {4, 6, 8}

def validate_veo_settings(model, generation_type, aspect_ratio, resolution, duration, image_urls):
    """Reglas cruzadas:
    - FIRST_AND_LAST_FRAMES_2_VIDEO: 1-2 image_urls, todos los modelos, duration 4/6/8.
    - REFERENCE_2_VIDEO: 1-3 image_urls, solo veo3_fast / veo3_lite, duration obligado a 8.
    - TEXT_2_VIDEO: image_urls vacío, todos los modelos, duration 4/6/8.
    - 4k requiere endpoint dedicado (no /veo/generate).
    """
```

---

## 4. Deprecación de Kling y TTS

### 4.1 Símbolos / módulos a marcar como deprecated (no borrar todavía)

| Símbolo                                                                                      | Archivo                                        | Acción                                                                                           |
| -------------------------------------------------------------------------------------------- | ---------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| `create_avatar_task`                                                                         | `KieGateway`, `KieClient`, `LimitedKieGateway` | Mantener para `VideosController` (pantalla standalone "Nuevo Video"). NO usar en workflow nuevo. |
| `create_kling_video_task`                                                                    | idem                                           | Idem.                                                                                            |
| `create_tts_task`                                                                            | idem                                           | Idem (pantalla "Generar Audio" standalone sigue usándolo).                                       |
| `StepType.A_ROLL` / `StepType.B_ROLL`                                                        | `domain/models.py`                             | Marcar deprecated en docstring; workflows existentes siguen corriendo con el path actual.        |
| `_run_a_roll` / `_run_b_roll_with_audio` / `_run_b_roll_silent` / `_run_b_roll_native_sound` | `WorkflowStepRunner`                           | Mantener para backward compat. NO documentar en nuevos workflows.                                |

### 4.2 Plan de retirada (en releases futuros)

- **v2.0.0** (próximo): introducir `veo-video` + workflows nuevos. Deprecar
  `a-roll`/`b-roll` con warning en logs cuando se carguen workflows que los
  usen.
- **v2.1.0**: remover el código de `a-roll`/`b-roll` del workflow runner (los
  métodos HTTP de `KieClient` se mantienen para las pantallas standalone).
- Cualquier workflow JSON del usuario que use `a-roll`/`b-roll` necesitará
  migración manual (script de conversión opcional).

---

## 5. Inconsistencias previas (siguen pendientes)

### 5.1 Bug de naming en `KieClient.create_nano_banana_task`

(Sin cambios desde la v1 del doc — sigue siendo deuda válida).

- El método se llama `create_nano_banana_task` pero acepta override del modelo
  vía `model=` kwarg. Workflow lo llama con `model="gpt-image-2-text-to-image"`.
- Propuesta: renombrar a `create_image_task` (genérico, acepta cualquier modelo
  de imagen del catálogo Kie).
- Ubicaciones:
  - `kie_avatar_studio/infra/kie_client.py:177-210`
  - `kie_avatar_studio/domain/ports.py:115-125`
  - `kie_avatar_studio/app_layer/limited_kie_gateway.py`
  - `kie_avatar_studio/app_layer/image_job_runner.py:152`

### 5.2 Docstrings desactualizados en `workflow_base_resolver.py`

(Sin cambios — siguen mintiendo con "Nano Banana 2" cuando ya usa GPT Image).

Líneas: 10, 168, 181.

### 5.3 Falta whitelist de modelos válidos para creación de modelo base

(Sin cambios — defensa para el futuro override).

---

## 6. Open questions para la usuaria

1. **Composición pre-VEO con Nano Banana 2** (forzosa, no opcional):
   `FIRST_AND_LAST_FRAMES_2_VIDEO` solo acepta 1-2 imágenes y la semántica es
   **first frame literal**, no "referencia". Si querés "modelo sosteniendo
   producto" en el frame inicial, **hay que pre-componer con Nano Banana 2** (no
   hay alternativa con este modo).
   - **Opción A** (recomendada): mantener flujo actual
     `GPT Image → Nano Banana (componer modelo+producto) → VEO con esa imagen compuesta`.
   - **Opción B**: skipear el producto y dejar que el prompt de ChatGPT Image ya
     incluya el producto al generar la modelo base.
   - **Opción C**: usar `last_frame` con la imagen del producto (transición
     "modelo → producto" en lugar de "modelo sosteniendo producto"), efecto
     visual distinto.

2. **¿Qué modelo VEO default?** `veo3_fast @ 720p` = 60 créditos. `veo3` Quality
   @ 720p = 250 créditos (4× más caro pero la mejor calidad de animación,
   accesible en FIRST_AND_LAST_FRAMES_2_VIDEO). `veo3_lite @ 720p` = 30 créditos
   para volumen.
   - **Mi recomendación**: `veo3_fast` default + override por workflow JSON.

3. **¿Qué resolución default?** 720p (60 créditos veo3_fast) cumple para reels
   verticales. 1080p (65 créditos) es solo +5 créditos. ¿Default a 1080p?
   ¿Configurable por workflow JSON?

4. **¿Qué duration default?** 4 / 6 / 8s. Con FIRST_AND_LAST_FRAMES_2_VIDEO los
   tres son válidos (ya no estás obligado a 8s como con REFERENCE). ¿Default 8s
   o configurable?

5. **¿Voice changer en este release o lo dejamos para v2.1?** Si va en v2.0:
   requiere FFmpeg local (extract audio) + cliente ElevenLabs directo
   (`/v1/speech-to-speech`). Si lo dejamos fuera: la usuaria hace el voice
   changer manualmente con la web/app de ElevenLabs después de descargar el MP4.
   **Mi recomendación: dejarlo fuera de v2.0** y enfocar el release en la
   migración a VEO. v2.1 mete voice changer como feature aditivo.

6. **¿Watermark?** El campo VEO lo soporta. ¿Lo exponemos en
   `pre_settings.veo.watermark` o lo dejamos `null` siempre?

7. **¿Mantener pantallas standalone "Nuevo Video" (Avatar Pro) y la pantalla de
   Kling 3.0 b-roll?** Útil como sandbox/testing, pero añade superficie de UI.
   ¿Las escondemos detrás de un toggle "modo legacy" en Settings, o las dejamos
   visibles?

8. **¿Migración de workflows existentes?** Los workflows
   `workflows/135-HairLossSolution.json` y `workflows/136-Noeramicomida.json`
   usan `a-roll`/`b-roll`. ¿Script de conversión auto o se reescriben a mano?

---

## 7. Plan de ataque sugerido (v2.0.0 — breaking)

### Fase 1 — Housekeeping (PR aparte, sin breaking)

- Rename `create_nano_banana_task` → `create_image_task`.
- Actualizar 3 docstrings desactualizados en `workflow_base_resolver.py`.
- Bump v1.4.1 (S) o esperar y meterlo en v2.0.0.

### Fase 2 — VEO 3.1 en KieClient (PR aparte, aditivo)

- Añadir `create_veo_video_task` + `get_veo_task_detail` + helper
  `poll_veo_task_for_url`.
- Tests `tests/test_kie_client_veo.py` con `httpx.MockTransport`.
- Constantes + validaciones en `policies.py`.
- Sin cambios al workflow runner aún.
- Bump v1.5.0 (M).

### Fase 3 — Workflow `veo-video` (PR mayor, breaking)

- `StepType.VEO_VIDEO`, `_run_veo`, schema JSON nuevo.
- ADR en `docs/adr/` justificando deprecación de Kling/TTS.
- Workflow de ejemplo en `workflows/example_veo_reel.json`.
- Documentar migración en `workflows/SCHEMA_REFERENCE.md`.
- Marcar `a-roll`/`b-roll` deprecated.
- Bump v2.0.0 (L — breaking).

### Fase 4 (opcional, v2.1) — Voice changer + FFmpeg

- Cliente ElevenLabs directo (`/v1/speech-to-speech`).
- Helper `extract_audio_from_video` (subprocess FFmpeg async).
- Step `voice-changer` opcional en workflows.

---

**Última actualización**: 2026-06-15 17:32. **Cambio importante v2 → v3**:
corregido el generation mode a `FIRST_AND_LAST_FRAMES_2_VIDEO` (era
`REFERENCE_2_VIDEO`). Implica: todos los modelos disponibles (incluye `veo3`
Quality), duraciones configurables 4/6/8, y **Nano Banana 2 sigue siendo
necesario** para componer modelo+producto en el frame inicial (REFERENCE iba a
evitar ese paso, FIRST_AND_LAST_FRAMES no). **Próxima acción**: la usuaria debe
responder las 8 preguntas de §6 antes de empezar Fase 1 o Fase 2.
