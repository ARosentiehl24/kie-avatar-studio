# Workflow JSON v2 — Referencia operativa

Este documento describe **qué JSON generar** para `workflows/*.json` en Kie
Avatar Studio (flujo v2).

Fuente de verdad machine-readable: [`SCHEMA.json`](./SCHEMA.json).

---

## 1) Contrato mínimo (lo que siempre debe existir)

El JSON raíz debe incluir exactamente:

1. `workflow` (string)
2. `pre_settings` (object)
3. `run` (array de pasos)

No agregues claves inventadas fuera del schema.

---

## 2) Estructura raíz

```json
{
  "workflow": "Nombre descriptivo",
  "pre_settings": {
    "model_creation": { "...": "..." }
  },
  "run": [{ "...": "step 1" }]
}
```

---

## 3) `pre_settings`

### 3.1 Campos principales (v2)

| Campo                 | Tipo                 | Requerido | Default  | Notas                                           |
| --------------------- | -------------------- | --------- | -------- | ----------------------------------------------- |
| `model_creation`      | object               | **Sí**    | —        | Define de dónde sale la imagen base del modelo. |
| `scene_approval_mode` | `"auto" \| "manual"` | No        | `"auto"` | Modo de aprobación de escenas.                  |
| `promote_product`     | boolean              | No        | `false`  | Si `true`, habilita uso de producto en steps.   |
| `image_aspect_ratio`  | string \| null       | No        | `null`   | Aspect ratio general opcional.                  |
| `veo`                 | object               | No        | `{}`     | Config global de VEO usada en todos los pasos.  |
| `voice_changer`       | object \| null       | No        | `null`   | Postproceso opcional (ElevenLabs STS).          |

### 3.2 `model_creation` (requerido)

Tres modos válidos:

#### A) `method: "prompt"` (recomendado si no hay assets locales)

```json
{
  "method": "prompt",
  "prompt": "Retrato de modelo femenina, estudio, luz suave..."
}
```

#### B) `method: "local"`

```json
{
  "method": "local",
  "local_path": "/ruta/a/imagen_modelo.png"
}
```

`local_path` puede omitirse para que la UI lo resuelva al encolar, pero para
generación autónoma se recomienda incluirlo.

#### C) `method: "catalog"`

```json
{
  "method": "catalog",
  "asset_kind": "generated",
  "asset_id": "img_abc123"
}
```

`asset_kind` solo admite `uploaded` o `generated`.

### 3.3 `veo` (opcional)

Todos los pasos de `run` se renderizan con VEO usando estos valores globales.

| Campo                | Tipo           | Default     | Valores                          |
| -------------------- | -------------- | ----------- | -------------------------------- |
| `model`              | string         | `veo3_fast` | `veo3`, `veo3_fast`, `veo3_lite` |
| `aspect_ratio`       | string         | `9:16`      | `16:9`, `9:16`, `Auto`           |
| `resolution`         | string         | `720p`      | `720p`, `1080p`, `4k`            |
| `duration`           | int            | `8`         | `4`, `6`, `8`                    |
| `enable_translation` | bool           | `true`      | `true/false`                     |
| `watermark`          | string \| null | `null`      | texto libre o `null`             |

### 3.4 `voice_changer` (opcional)

Si existe, debe incluir `voice_id` no vacío.

```json
{
  "voice_id": "JBFqnCBsd6RMkjVDRZzb",
  "model_id": "eleven_multilingual_sts_v2",
  "remove_background_noise": true,
  "output_format": "mp3_44100_128",
  "voice_settings": {
    "stability": 0.75,
    "similarity_boost": 0.85,
    "style": 0.0,
    "speed": 1.0
  }
}
```

`voice_settings` es opcional y se envía como JSON string al endpoint directo de
ElevenLabs speech-to-speech. `language_code` no aplica a este postproceso STS.

---

## 4) `run[]` (pasos)

Cada item del array representa una escena.

| Campo                | Tipo                   | Req.        | Default | Notas                                                    |
| -------------------- | ---------------------- | ----------- | ------- | -------------------------------------------------------- |
| `step`               | int                    | **Sí**      | —       | Debe comenzar en 1 y avanzar correlativo.                |
| `scene_name`         | string                 | **Sí**      | —       | Nombre legible de escena.                                |
| `type`               | `"a-roll" \| "b-roll"` | **Sí**      | —       | Clasificación narrativa del paso.                        |
| `prompt`             | string                 | **Sí**      | —       | Prompt visual del clip (siempre requerido).              |
| `text`               | string                 | Condicional | —       | **Obligatorio para `a-roll`**.                           |
| `attached`           | bool                   | No          | `true`  | Si `false`, genera clip pero no entra a `final.mp4`.     |
| `change_scene`       | bool                   | No          | `true`  | Control de cambio de escena.                             |
| `scene_description`  | string                 | No          | `""`    | Si `b-roll` + `change_scene=true`, debe tener contenido. |
| `include_product`    | bool                   | No          | `false` | Requiere `pre_settings.promote_product=true`.            |
| `include_model`      | bool                   | No          | `true`  | Incluye modelo base en el render.                        |
| `set_as_base`        | bool                   | No          | `false` | Si `true`, esta escena se vuelve la nueva base para los siguientes steps. |
| `product_prompt`     | string                 | No          | `""`    | Prompt específico para producto.                         |
| `image_aspect_ratio` | string \| null         | No          | `null`  | Override por step (aspect ratios generales).             |

Campos legacy por compatibilidad en step:

- `voiceover` (legacy)
- `duration_seconds` (legacy)
- `change_background` (alias legacy de `change_scene`)
- `background_description` (alias legacy de `scene_description`)

---

## 5) Reglas de validación críticas

1. `run` no puede estar vacío.
2. Los `step` deben ser consecutivos (1, 2, 3, ...).
3. `a-roll` requiere `text` no vacío.
4. `prompt`:
   - `a-roll`: máximo 5000 chars
   - `b-roll`: máximo 2500 chars
5. Si `b-roll` y `change_scene=true`, `scene_description` debe ser no vacío.
6. Si `include_product=true`, entonces `pre_settings.promote_product` debe ser
   `true`.
7. Aspect ratios deben estar en el set permitido del dominio:
   - `auto`, `match_input_image`, `1:1`, `9:16`, `16:9`, `4:3`, `3:4`, `3:2`,
     `2:3`, `4:5`, `5:4`, `21:9`, `9:21`, `1:8`, `4:1`, `8:1`

---

## 6) Semántica de ejecución v2 (importante para expectativas)

1. **Todos los pasos** se renderizan vía VEO (no se separa por motor según
   tipo).
2. La app concatena solo clips `attached=true` para producir `final.mp4`.
3. Luego extrae audio a `final_audio.mp3`.
4. Si hay `voice_changer`, genera `voice_changed_audio.mp3` como postproceso.
5. Campos legacy se aceptan por compatibilidad, pero no definen el camino
   principal v2.
6. Si algún step usa `set_as_base=true`, la ejecución pasa a modo secuencial
   para mantener orden determinista en la continuidad de escenas.
7. En reanudaciones/re-encolados, la base efectiva se reconstruye usando la
   última `scene_image` promovida y completada (`set_as_base=true`).

---

## 7) No incluir en JSON generado

No generes campos internos/resueltos en runtime (ejemplos):

- `scene_slug`
- `product_image`
- `resolved_image_ref`

---

## 8) Plantilla recomendada (v2)

```json
{
  "workflow": "UGC - Producto X - 3 escenas",
  "pre_settings": {
    "model_creation": {
      "method": "prompt",
      "prompt": "Modelo femenina latina de 28 años, look natural de skincare."
    },
    "scene_approval_mode": "auto",
    "promote_product": true,
    "veo": {
      "model": "veo3_fast",
      "aspect_ratio": "9:16",
      "resolution": "720p",
      "duration": 8,
      "enable_translation": true
    },
    "voice_changer": {
      "voice_id": "JBFqnCBsd6RMkjVDRZzb",
      "model_id": "eleven_multilingual_sts_v2",
      "remove_background_noise": true,
      "output_format": "mp3_44100_128",
      "voice_settings": {
        "stability": 0.75,
        "similarity_boost": 0.85,
        "style": 0.0,
        "speed": 1.0
      }
    }
  },
  "run": [
    {
      "step": 1,
      "scene_name": "Hook",
      "type": "a-roll",
      "prompt": "Primer plano, luz natural, expresión sorprendida.",
      "text": "¿Tu piel perdió brillo en pocas semanas?",
      "attached": true,
      "change_scene": true,
      "scene_description": "Baño moderno, mañana",
      "include_model": true,
      "include_product": false
    },
    {
      "step": 2,
      "scene_name": "Demostración",
      "type": "b-roll",
      "prompt": "Hands-on aplicando crema sobre mejilla con textura visible.",
      "attached": true,
      "change_scene": true,
      "scene_description": "Tocador minimalista con luz suave",
      "set_as_base": true,
      "include_model": true,
      "include_product": true,
      "product_prompt": "Frasco blanco mate con etiqueta verde menta"
    },
    {
      "step": 3,
      "scene_name": "CTA",
      "type": "a-roll",
      "prompt": "Plano medio vertical, sonrisa, gesto señalando producto.",
      "text": "Pruébalo hoy y siente la diferencia desde la primera aplicación.",
      "attached": true,
      "change_scene": false,
      "include_model": true,
      "include_product": true
    }
  ]
}
```
