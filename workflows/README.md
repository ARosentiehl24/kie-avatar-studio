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
      "scene_description": "",
      "set_as_base": false,
      "prompt": "Plano medio mujer hablando a cámara",
      "text": "Por fin entendí qué tenía y necesito contártelo.",
    },
  ],
}
```

## Tipos de step (`type`)

### `a-roll` — la modelo habla a cámara (VEO 3.1)

| Componente generado                                                      | Path en el output dir      |
| ------------------------------------------------------------------------ | -------------------------- |
| Imagen scene (Nano Banana 2 si `change_scene=true`, sino reutiliza base) | `step_NN_<slug>/step_NN_<slug>_scene.png` |
| Video VEO 3.1 con audio nativo                                           | `step_NN_<slug>/step_NN_<slug>_video.mp4` |

**Requiere `text` no vacío** (lo que la modelo debe decir en VEO).

### `b-roll` — apoyo visual/producto/infografía (VEO 3.1)

| Componente generado                                                      | Path en el output dir      |
| ------------------------------------------------------------------------ | -------------------------- |
| Imagen scene obligatoria para JSON generado por IA (`change_scene=true`) | `step_NN_<slug>/step_NN_<slug>_scene.png` |
| Video VEO 3.1 con audio nativo o voz en off                              | `step_NN_<slug>/step_NN_<slug>_video.mp4` |

Reglas para JSON generado por IA:

- Todo `b-roll` debe usar `change_scene=true`.
- Todo `b-roll` debe traer `scene_description` no vacío.
- Si `include_product=true`, también requiere `pre_settings.promote_product=true`
  y `product_prompt` no vacío.

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

> ⚠️ No generes b-roll con `change_scene=false`. Eso reusa la base de la
> modelo y produce escenas auxiliares/producto/infografía incorrectas. El
> `SCHEMA.json` estricto lo marca inválido para generación por IA.

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
├── e2e_test_3_steps_base.png          ← imagen base de la modelo
├── step_01_hook_1/
│   ├── step_01_hook_1_scene.png
│   └── step_01_hook_1_video.mp4       ← VEO 3.1
├── step_02_b_roll_pain/
│   ├── step_02_b_roll_pain_scene.png
│   └── step_02_b_roll_pain_video.mp4
├── step_03_product_reveal/
│   ├── step_03_product_reveal_scene.png
│   └── step_03_product_reveal_video.mp4
├── e2e_test_3_steps_final.mp4         ← concat de clips attached
└── e2e_test_3_steps_final_audio.mp3   ← audio extraído del final
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
- Por cada step con `change_scene=true`: 1 imagen Nano Banana 2 adicional.
- Por cada step: 1 video VEO 3.1.
- Postproceso local con FFmpeg: concat + extracción de audio.
- Opcional: 1 llamada ElevenLabs speech-to-speech si `voice_changer` está
  configurado.

El modal de confirmación muestra el desglose de operaciones + tu saldo actual de
Kie antes de ejecutar.

## Escribe tu propio workflow

Puedes crear tus propios archivos JSON en esta carpeta siguiendo la estructura
descrita en `workflows/SCHEMA.json` o la guía de referencia detallada en
`workflows/SCHEMA_REFERENCE.md`.
