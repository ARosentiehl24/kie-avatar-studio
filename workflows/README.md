# Workflows declarativos (`workflows/`)

Esta carpeta contiene definiciones JSON de **workflows de automatización** que
la pantalla `Automatización` (hotkey **F**) escanea y ofrece para ejecutar
end-to-end.

Cada archivo `.json` es **un workflow**. La pantalla escanea al abrir y al
presionar **R** para refrescar.

## Estructura mínima del JSON

```jsonc
{
  "workflow": "Video Creation Automation", // → slug filesystem-safe
  "pre_settings": {
    "model_creation": {
      "method": "prompt", // "prompt" | "local" | "catalog"
      "prompt": "Photorealistic Latina woman talking to camera…",
      // "local_path": "inputs/modelo.png"        // si method=local
      // "asset_kind": "generated",               // si method=catalog
      // "asset_id": "img_20260605_abc123"        // si method=catalog
    },
  },
  "run": [
    {
      "step": 1,
      "scene_name": "Hook 1",
      "type": "a-roll", // "a-roll" | "b-roll"
      "change_scene": false,
      "scene_description": "Soft kitchen, natural light",
      "set_as_base": false,
      "prompt": "Plano medio mujer hablando a cámara",
      "text": "Por fin entendí qué tenía y necesito contártelo.",
    },
  ],
}
```

## Tipos de step (`type`)

### `a-roll` — la modelo habla a cámara (lip-sync)

| Componente generado                                                                 | Path en el output dir      |
| ----------------------------------------------------------------------------------- | -------------------------- |
| Imagen scene (Nano Banana 2 refit si `change_scene=true`, sino reutiliza base) | `step_NN_<slug>/scene.png` |
| Audio TTS                                                                           | `step_NN_<slug>/audio.mp3` |
| Video Avatar Pro (con audio sincronizado)                                           | `step_NN_<slug>/final.mp4` |

**Requiere `text` no vacío** (el script que la modelo dice).

### `b-roll` con `text` no vacío — video silencioso + audio separado

| Componente generado                                            | Path en el output dir      |
| -------------------------------------------------------------- | -------------------------- |
| Imagen scene (Nano Banana 2 refit si `change_scene=true`) | `step_NN_<slug>/scene.png` |
| Audio TTS (para post-producción)                               | `step_NN_<slug>/audio.mp3` |
| Video Kling 3.0 b-roll (silencioso)                            | `step_NN_<slug>/video.mp4` |

### `b-roll` con `text == ""` — solo video silencioso

| Componente generado                 | Path en el output dir      |
| ----------------------------------- | -------------------------- |
| Imagen scene                        | `step_NN_<slug>/scene.png` |
| Video Kling 3.0 b-roll (silencioso) | `step_NN_<slug>/video.mp4` |

## `model_creation.method`

- `prompt`: genera la imagen base con GPT Image 2 (campo `prompt` requerido).
- `local`: sube una imagen del filesystem (campo `local_path` requerido, ej.
  `inputs/modelo.png`). El archivo se valida al encolar Y justo antes del upload
  (mitiga la race del archivo movido).
- `catalog`: reusa una imagen ya en el catálogo (campos `asset_kind`
  `"uploaded"`|`"generated"` + `asset_id` requeridos).

## `change_scene`

- `false`: el step reusa la imagen base de la modelo (`base.png`) tal cual. Útil
  cuando la modelo aparece en la misma habitación que la imagen base.
- `true`: genera una imagen scene nueva con Nano Banana 2 usando la base como
  referencia + el `scene_description` + el `prompt` del step.

> ⚠️ B-roll con `change_scene=false` usará la cara de la modelo como imagen
> base del video, lo cual probablemente NO es lo que querés para escenas
> auxiliares (jeans, ilustración del intestino, etc). El validator emite un
> warning visible en la UI.

## `set_as_base`

- `false` (default): la base del workflow se mantiene en la imagen inicial
  (`base.png`) para los siguientes steps.
- `true`: la `scene_image` generada por ese step pasa a ser la nueva base para
  los siguientes steps (continuidad de locación).

> ℹ️ Si un workflow usa `set_as_base=true` en algún step, la ejecución se hace
> secuencial para respetar el orden de promoción de base.

## Outputs y manifest

Cada ejecución crea un directorio único bajo
`outputs/wf_<timestamp>_<short_uuid>/` con esta estructura:

```text
outputs/wf_20260606_abc123/
├── workflow.json          ← manifest atómico (snapshot derivado de la DB)
├── base.png               ← imagen base de la modelo (descargada eager)
├── step_01_hook_1/
│   ├── scene.png
│   ├── audio.mp3          ← audio TTS separado para post
│   └── final.mp4          ← a-roll
├── step_02_b_roll_pain/
│   ├── scene.png
│   ├── audio.mp3          ← b-roll con texto
│   └── video.mp4
└── step_03_product_reveal/
    ├── scene.png
    └── video.mp4          ← b-roll silencioso
```

El `workflow.json` se **regenera atómicamente** en cada transición del workflow.
La DB de la app es la fuente de verdad runtime; el manifest es un snapshot
derivado para inspección a mano o por consumers externos (scripts
post-producción, dashboards, etc.).

**Importante**: si la app está corriendo, el manifest puede ir 1–2 transiciones
atrás de la DB (la regeneración no es síncrona con cada escritura SQL pero está
cerca). Si la app está apagada, el manifest refleja el último estado conocido al
cierre limpio.

## Costos

Un workflow de N steps consume:

- 1 imagen GPT Image 2 para la base (si `method=prompt`).
- Por cada step con `change_background=true`: 1 imagen Nano Banana 2 adicional.
- Por cada step `a-roll`: 1 TTS turbo + 1 Avatar Pro.
- Por cada step `b-roll con text`: 1 TTS turbo + 1 b-roll Kling 3.0.
- Por cada step `b-roll sin text`: 1 b-roll Kling 3.0.

El modal de confirmación muestra el desglose de operaciones + tu saldo actual de
Kie antes de ejecutar.

## Escribe tu propio workflow

Puedes crear tus propios archivos JSON en esta carpeta siguiendo la estructura
descrita en `workflows/SCHEMA.json` o la guía de referencia detallada en
`workflows/SCHEMA_REFERENCE.md`.
