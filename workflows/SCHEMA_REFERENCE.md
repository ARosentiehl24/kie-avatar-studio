# Workflow JSON v2 — guía para IA generadora

Este documento explica **cómo generar un workflow JSON válido y útil** para Kie
Avatar Studio. Está pensado para que una IA pueda crear videos narrativos con
VEO 3.1 siguiendo el estilo del ejemplo
[`000-SANITY-MATRIX-v2.json`](./000-SANITY-MATRIX-v2.json).

Fuente machine-readable: [`SCHEMA.json`](./SCHEMA.json).

> **Importante para IAs generadoras:** esta guía y `SCHEMA.json` son el contrato
> de generación. El loader puede tolerar algunos JSON legacy, pero una IA debe
> generar siempre el formato estricto documentado aquí.

## Objetivo del JSON

Un workflow describe un video como una lista ordenada de escenas:

```text
modelo base + steps narrativos -> VEO 3.1 con audio nativo
                               -> concat local de clips attached
                               -> voice changer ElevenLabs opcional
```

Al generar JSON, la IA debe decidir:

1. Quién o qué aparece como base visual.
2. Qué escena ocurre en cada step.
3. Qué texto se habla o narra.
4. Si la escena cambia de fondo, incluye producto o solo apoya visualmente.
5. Si la escena entra al `final.mp4`.
6. Si el audio final se queda como VEO lo genera o si se convierte con ElevenLabs.

Usa **prompts y textos 100% en español**.

## Contrato raíz

```json
{
  "workflow": "Nombre descriptivo del video",
  "pre_settings": { "model_creation": { "method": "prompt", "prompt": "..." } },
  "run": []
}
```

| Campo | Obligatorio | Qué debe contener |
| --- | --- | --- |
| `workflow` | Sí | Título editorial claro. Ej: `"UGC digestivo - 5 escenas"`. |
| `pre_settings` | Sí | Configuración global: base, VEO, producto, voz. |
| `run` | Sí | Array ordenado de steps. Debe empezar en `step: 1` y ser consecutivo. |

No inventes claves fuera del schema.

## `pre_settings`

### `model_creation` (requerido)

Define la imagen base del workflow. Esa base determina identidad visual,
rostro, vestuario, encuadre inicial y estilo general.

#### `method: "prompt"`

Usar cuando la IA debe crear una modelo/base nueva.

```json
{
  "method": "prompt",
  "prompt": "Fotografía hiperrealista vertical 9:16 de mujer latina de 29 años..."
}
```

El `prompt` debe describir:

- persona/modelo o sujeto principal;
- edad aproximada, rasgos generales, vestuario;
- encuadre (`plano medio`, `primer plano`, etc.);
- fondo, luz, estética y formato.

#### `method: "local"`

Usar si ya existe una imagen local.

```json
{ "method": "local", "local_path": "inputs/modelo.png" }
```

Si `local_path` se omite, la UI puede pedirlo al encolar; para generación
autónoma, inclúyelo.

#### `method: "catalog"`

Usar si ya existe un asset en el catálogo de la app.

```json
{ "method": "catalog", "asset_kind": "generated", "asset_id": "img_abc123" }
```

No inventes `asset_id`; debe existir.

### `veo`

Configuración global de render VEO.

| Campo | Valores | Recomendación |
| --- | --- | --- |
| `model` | `veo3`, `veo3_fast`, `veo3_lite` | `veo3_fast` para iterar. |
| `aspect_ratio` | `9:16`, `16:9`, `Auto` | `9:16` para Reels/TikTok/Shorts. |
| `resolution` | `720p`, `1080p`, `4k` | `720p` para costo/velocidad. |
| `duration` | `4`, `6`, `8` | `8` para diálogo; `4/6` para b-roll corto. |
| `enable_translation` | boolean | `true` para español. |
| `watermark` | string/null | `null` si no quieres marca. |

### `scene_approval_mode`

- `"auto"`: ejecuta sin pausar. Más rápido.
- `"manual"`: pausa b-rolls que generan `scene_image` para aprobar/regenerar
  antes de gastar VEO. Útil cuando el producto o escena puede salir mal.

### `promote_product`

Debe ser `true` si algún step usa `include_product: true`.

### `image_aspect_ratio`

Aspect ratio global para imágenes generadas/compuestas con Nano Banana. Para
workflow vertical usa `"9:16"`. Cada step puede sobrescribirlo con
`image_aspect_ratio`.

### `voice_changer`

Postproceso opcional con ElevenLabs Speech-to-Speech sobre `final_audio.mp3`.
El workflow **funciona completo sin ElevenLabs**: si `voice_changer` es `null`
o se omite, la app termina después de crear `final.mp4` y `final_audio.mp3`.
Eso permite que el usuario procese el audio manualmente en ElevenLabs u otra
herramienta externa.

Para desactivar ElevenLabs desde JSON:

```json
"voice_changer": null
```

O simplemente omite la propiedad, porque su default es `null`.

Para activar ElevenLabs dentro de la app:

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

Notas:

- `voice_id` debe venir del catálogo/listado de ElevenLabs.
- No uses `language_code` aquí; no aplica a STS.
- Para consistencia: `stability` 0.70-0.90, `similarity_boost` 0.80-0.95,
  `style` 0.0-0.2, `speed` 0.95-1.05.
- Si `voice_changer` está configurado pero no hay `ELEVENLABS_API_KEY`, ese
  postproceso no podrá ejecutarse. Si no quieres depender de ElevenLabs, usa
  `voice_changer: null`.

## `run[]`: steps/escenas

Cada item de `run` representa una escena.

| Campo | Requerido | Qué escribir |
| --- | --- | --- |
| `step` | Sí | Número consecutivo desde 1. |
| `scene_name` | Sí | Nombre corto: `"Hook testimonial"`, `"Solo producto"`, `"CTA"`. |
| `type` | Sí | `"a-roll"` o `"b-roll"`. |
| `prompt` | Sí | Prompt visual detallado para VEO. |
| `text` | A-roll sí | Guion hablado exacto o voz en off. |
| `attached` | No | `true` si entra al `final.mp4`; `false` si es clip suelto. |
| `change_scene` | B-roll sí | `true` genera nueva scene image; en b-roll debe ser siempre `true`. |
| `scene_description` | B-roll sí | Lugar/fondo/luz/ambiente. Obligatorio si `change_scene=true`. |
| `include_product` | No | `true` si el producto aparece en este step. |
| `include_model` | No | `true` si aparece la modelo/persona base. |
| `set_as_base` | No | `true` si esta scene image será base para steps siguientes. |
| `product_prompt` | Si producto | Obligatorio y no vacío si `include_product=true`. |
| `image_aspect_ratio` | No | Override de imagen para este step. |

### `type: "a-roll"`

Usar para la persona/modelo hablando a cámara.

Reglas:

- `text` es obligatorio y debe ser breve.
- `prompt` debe describir plano, gesto, emoción, cámara, luz y continuidad.
- Si la misma modelo continúa, menciona continuidad visual.
- Si `change_scene=true`, `scene_description` es obligatorio y debe describir
  el entorno/fondo nuevo.

Ejemplo:

```json
{
  "step": 1,
  "scene_name": "Hook testimonial",
  "type": "a-roll",
  "attached": true,
  "change_scene": false,
  "prompt": "Plano medio vertical, cámara a la altura de los ojos, expresión cercana...",
  "text": "Te cuento rápido mi experiencia: llevaba semanas con inflamación..."
}
```

### `type: "b-roll"`

Usar para producto, manos, infografías, ambiente, close-ups o apoyo visual.

Reglas:

- `text` puede estar vacío o contener voz en off.
- Para JSON generado por IA, `b-roll` **siempre** debe usar
  `change_scene: true`.
- `scene_description` es obligatorio, no vacío y debe describir el set visual:
  lugar, fondo, luz, superficie, props, gráfica/infografía o contexto del
  producto.
- Si `include_model=false`, no debe aparecer la modelo completa.
- Una escena “solo producto” puede incluir manos humanas interactuando si el
  foco sigue siendo el producto.
- Si `include_product=true`, también debes poner
  `pre_settings.promote_product: true` y `product_prompt` no vacío.
- No generes b-roll con `change_scene=false` ni con `scene_description=""`;
  eso reusa la base de la modelo y produce resultados inválidos para escenas
  auxiliares/producto/infografía.

Ejemplo solo producto:

```json
{
  "step": 3,
  "scene_name": "Solo producto con narración",
  "type": "b-roll",
  "attached": true,
  "change_scene": true,
  "scene_description": "Mesa de cocina clara con luz natural cálida, fondo limpio y superficie ordenada para close-up de producto.",
  "prompt": "Toma protagonista del suplemento digestivo en polvo, cámara macro...",
  "text": "Aquí te enseño el suplemento digestivo en detalle...",
  "include_product": true,
  "include_model": false,
  "product_prompt": "Frasco ámbar bajo con tapa café mate, etiqueta crema visible..."
}
```

## Cómo escribir buenos prompts

Un buen `prompt` de step debe incluir:

1. Tipo de plano: `plano medio`, `primer plano`, `macro`, `infografía`.
2. Acción visible: qué hace la modelo, manos o producto.
3. Cámara/movimiento: `cámara fija`, `desplazamiento suave`, `acercamiento`.
4. Luz/estética: `luz natural cálida`, `realista`, `UGC premium`.
5. Continuidad: misma modelo, mismo vestuario, mismo producto, mismo fondo si aplica.
6. Voz para VEO, si quieres probar consistencia previa al voice changer.

Plantilla de instrucción de voz para VEO:

```text
Instrucción de voz para VEO: mantener en todos los clips una única voz femenina
latina adulta, español latino neutro, timbre cálido y suave, tono testimonial
cercano, ritmo natural pausado, misma energía y mismo acento; no cambiar de
locutor ni de timbre entre escenas.
```

Para b-roll sin persona:

```text
Instrucción de voz para VEO: usar voz en off con la misma voz femenina latina
adulta de los demás clips...
```

## Patrón narrativo recomendado

Para un video UGC de 5 escenas:

1. **Hook a-roll**: modelo habla a cámara y plantea problema/beneficio.
2. **Cambio de escena a-roll**: misma modelo en otra locación, refuerza continuidad.
3. **Solo producto b-roll**: close-up del producto, manos opcionales, narración.
4. **Modelo + producto a-roll**: testimonio directo con producto en mano.
5. **Infografía / explicación b-roll**: apoyo visual sin modelo o cierre educativo.

## Reglas críticas de validación

1. `run` no puede estar vacío.
2. `step` debe ser consecutivo: 1, 2, 3...
3. `a-roll` requiere `text` no vacío.
4. Todo `b-roll` generado por IA requiere `change_scene=true`.
5. Todo `b-roll` requiere `scene_description` no vacío.
6. Todo step con `change_scene=true` requiere `scene_description` no vacío.
7. Todo step con `include_product=true` requiere:
   - `pre_settings.promote_product=true`;
   - `product_prompt` no vacío.
8. Todo step con `set_as_base=true` requiere:
   - `change_scene=true`;
   - `scene_description` no vacío.
9. `prompt` máximo:
   - a-roll: 5000 caracteres;
   - b-roll: 2500 caracteres.
10. Si usas `set_as_base=true`, el workflow se ejecuta en serie para preservar
   continuidad.
11. `voice_changer` es opcional: `null` u omitido significa “no llamar a
   ElevenLabs”.

## Outputs esperados

Sin ElevenLabs (`voice_changer: null` u omitido):

- `final.mp4`
- `final_audio.mp3`

Con ElevenLabs (`voice_changer` configurado):

- `final.mp4`
- `final_audio.mp3`
- `voice_changed_audio.mp3`

`voice_changed_audio.mp3` es un derivado opcional. No lo esperes si el usuario
quiere hacer el voice changer manualmente fuera de la app.

## No incluir en JSON generado

No generes campos runtime/internos:

- `scene_slug`
- `product_image`
- `resolved_image_ref`
- `video_task_id`
- `scene_image_path`
- `progress`
- `status`

## Plantilla completa recomendada

```json
{
  "workflow": "UGC - Producto X - 5 escenas",
  "pre_settings": {
    "model_creation": {
      "method": "prompt",
      "prompt": "Fotografía hiperrealista vertical 9:16 de mujer latina de 29 años..."
    },
    "scene_approval_mode": "manual",
    "promote_product": true,
    "image_aspect_ratio": "9:16",
    "veo": {
      "model": "veo3_fast",
      "aspect_ratio": "9:16",
      "resolution": "720p",
      "duration": 8,
      "enable_translation": true,
      "watermark": null
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
      "scene_name": "Hook testimonial",
      "type": "a-roll",
      "attached": true,
      "change_scene": false,
      "scene_description": "",
      "prompt": "Plano medio vertical, cámara a la altura de los ojos, tono testimonial cercano.",
      "text": "Te cuento rápido mi experiencia: por fin encontré una rutina que sí me ayudó.",
      "include_product": false,
      "include_model": true,
      "set_as_base": false,
      "product_prompt": ""
    },
    {
      "step": 2,
      "scene_name": "Solo producto con narración",
      "type": "b-roll",
      "attached": true,
      "change_scene": true,
      "scene_description": "Mesa de cocina limpia con luz natural lateral, fondo neutro desenfocado y espacio para destacar el producto.",
      "prompt": "Toma macro del producto con textura visible, fondo limpio, manos opcionales interactuando sin perder foco.",
      "text": "Aquí te enseño el producto en detalle para que veas exactamente lo que estoy usando.",
      "include_product": true,
      "include_model": false,
      "set_as_base": false,
      "product_prompt": "Producto centrado, etiqueta legible, empaque nítido, mano humana mostrando uso real.",
      "image_aspect_ratio": "1:1"
    }
  ]
}
```

Si el usuario quiere hacer ElevenLabs manualmente, reemplaza ese bloque por:

```json
"voice_changer": null
```
