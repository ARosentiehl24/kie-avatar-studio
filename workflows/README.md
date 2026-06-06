# Workflows declarativos (`workflows/`)

Esta carpeta contiene definiciones JSON de **workflows de automatización** que la
pantalla `Automatización` (hotkey **F**) escanea y ofrece para ejecutar end-to-end.

Cada archivo `.json` es **un workflow**. La pantalla escanea al abrir y al
presionar **R** para refrescar.

## Estructura mínima del JSON

```jsonc
{
  "workflow": "Video Creation Automation",      // → slug filesystem-safe
  "pre_settings": {
    "audio_language": "es-419",                  // BCP 47, opcional
    "voice_preset": "latina_warm_authentic",     // ID del preset (debe existir en Presets)
    "model_creation": {
      "method": "prompt",                        // "prompt" | "local" | "catalog"
      "prompt": "Photorealistic Latina woman talking to camera…"
      // "local_path": "inputs/modelo.png"        // si method=local
      // "asset_kind": "generated",               // si method=catalog
      // "asset_id": "img_20260605_abc123"        // si method=catalog
    }
  },
  "run": [
    {
      "step": 1,
      "scene_name": "Hook 1",
      "type": "a-roll",                          // "a-roll" | "b-roll"
      "change_background": false,
      "background_description": "Soft kitchen, natural light",
      "prompt": "Plano medio mujer hablando a cámara",
      "text": "Por fin entendí qué tenía y necesito contártelo."
    }
  ]
}
```

## Tipos de step (`type`)

### `a-roll` — la modelo habla a cámara (lip-sync)

| Componente generado | Path en el output dir |
|---|---|
| Imagen scene (Nano Banana refit si `change_background=true`, sino reutiliza base) | `step_NN_<slug>/scene.png` |
| Audio TTS | (no se descarga aparte — queda embebido) |
| Video Avatar Pro (con audio sincronizado) | `step_NN_<slug>/final.mp4` |

**Requiere `text` no vacío** (el script que la modelo dice).

### `b-roll` con `text` no vacío — video silencioso + audio separado

| Componente generado | Path en el output dir |
|---|---|
| Imagen scene (Nano Banana refit si `change_background=true`) | `step_NN_<slug>/scene.png` |
| Audio TTS (para post-producción) | `step_NN_<slug>/audio.mp3` |
| Video Kling 2.6 i2v (silencioso) | `step_NN_<slug>/video.mp4` |

### `b-roll` con `text == ""` — solo video silencioso

| Componente generado | Path en el output dir |
|---|---|
| Imagen scene | `step_NN_<slug>/scene.png` |
| Video Kling 2.6 i2v (silencioso) | `step_NN_<slug>/video.mp4` |

## `model_creation.method`

- `prompt`: genera la imagen base con Nano Banana 2 (campo `prompt` requerido).
- `local`: sube una imagen del filesystem (campo `local_path` requerido, ej.
  `inputs/modelo.png`). El archivo se valida al encolar Y justo antes del
  upload (mitiga la race del archivo movido).
- `catalog`: reusa una imagen ya en el catálogo (campos `asset_kind`
  `"uploaded"`|`"generated"` + `asset_id` requeridos).

## `change_background`

- `false`: el step reusa la imagen base de la modelo (`base.png`) tal cual.
  Útil cuando la modelo aparece en la misma habitación que la imagen base.
- `true`: genera una imagen scene nueva con Nano Banana 2 usando la base
  como referencia + el `background_description` + el `prompt` del step.

> ⚠️ B-roll con `change_background=false` usará la cara de la modelo como
> imagen base del video, lo cual probablemente NO es lo que querés para
> escenas auxiliares (jeans, ilustración del intestino, etc). El validator
> emite un warning visible en la UI.

## `audio_language`

- Si está seteado (ej. `"es-419"`, `"pt-BR"`, `"en"`): la app fuerza el
  modelo TTS turbo (`elevenlabs/text-to-speech-turbo-v2-5`), que acepta
  `language_code`.
- Si es `null` o `""`: la app usa el modelo multilingual default
  (`elevenlabs/text-to-speech-multilingual-v2`), que NO acepta
  `language_code` y respondería 422 si se le mandara.

## Outputs y manifest

Cada ejecución crea un directorio único bajo `outputs/wf_<timestamp>_<short_uuid>/`
con esta estructura:

```
outputs/wf_20260606_abc123/
├── workflow.json          ← manifest atómico (snapshot derivado de la DB)
├── base.png               ← imagen base de la modelo (descargada eager)
├── step_01_hook_1/
│   ├── scene.png
│   └── final.mp4          ← a-roll
├── step_02_b_roll_pain/
│   ├── scene.png
│   ├── audio.mp3          ← b-roll con texto
│   └── video.mp4
└── step_03_product_reveal/
    ├── scene.png
    └── video.mp4          ← b-roll silencioso
```

El `workflow.json` se **regenera atómicamente** en cada transición del
workflow. La DB de la app es la fuente de verdad runtime; el manifest es
un snapshot derivado para inspección a mano o por consumers externos
(scripts post-producción, dashboards, etc.).

**Importante**: si la app está corriendo, el manifest puede ir 1–2
transiciones atrás de la DB (la regeneración no es síncrona con cada
escritura SQL pero está cerca). Si la app está apagada, el manifest
refleja el último estado conocido al cierre limpio.

## Costos

Un workflow de N steps consume:

- 1 imagen Nano Banana 2 para la base (si `method=prompt`).
- Por cada step con `change_background=true`: 1 imagen Nano Banana adicional.
- Por cada step `a-roll`: 1 TTS turbo + 1 Avatar Pro.
- Por cada step `b-roll con text`: 1 TTS turbo + 1 i2v Kling 2.6.
- Por cada step `b-roll sin text`: 1 i2v Kling 2.6.

El modal de confirmación muestra el desglose de operaciones + tu saldo
actual de Kie antes de ejecutar.

## Ejemplo completo

Ver `workflows/example_video_creation_automation.json` para un workflow
realista de 7 steps (4 a-rolls + 3 b-rolls) basado en el caso de uso
canónico de probiótico/digestivo.
