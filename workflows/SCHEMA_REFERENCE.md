# Workflow JSON — Reference para generar workflows con IA

Este documento describe TODOS los campos válidos del JSON de workflows de
**Kie Avatar Studio**, con valores aceptados, defaults y restricciones.

Pensado para pegarse al prompt de una IA (ChatGPT, Claude, Gemini) para que
genere instancias compatibles. La validación final corre en
`kie_avatar_studio/domain/policies.py` y `infra/workflow_loader.py`.

> **Tip para el prompt**: pegá este archivo COMPLETO al inicio del system
> prompt, y después describí el video que querés. Pedile a la IA que devuelva
> SOLO el JSON sin markdown wrappers.

---

## 1. Estructura general

```jsonc
{
  "workflow": "<string, nombre humano del workflow>",
  "pre_settings": { ... },
  "run": [ { step 1 }, { step 2 }, ... ]
}
```

Validación: archivo debe parsear como JSON válido y debe estar bajo
`workflows/*.json` para que el escáner del app lo encuentre.

---

## 2. `pre_settings` — configuración global del workflow

```jsonc
{
  "pre_settings": {
    "audio_language": "<ISO 639-1 | null>",
    "voice_preset": "<string id o label de preset>",
    "i2v_duration_seconds": "<entero 3-15 | null>",
    "scene_approval_mode": "<auto | manual>",
    "promote_product": "<true | false>",
    "image_aspect_ratio": "<auto | 1:1 | 9:16 | 16:9 | ...>",
    "model_creation": { ... }
  }
}
```

| Campo | Tipo | Valores | Default | Notas |
|---|---|---|---|---|
| `audio_language` | string \| null | ISO 639-1 ("es", "en", "es-419", "pt", "fr", "de", "it", "ja", "ko", "zh", "ar", "hi", ...) o `null` | `null` | Si `null` → multilingual-v2 (estable, auto-detecta idioma). Si seteado → fuerza turbo-2-5 (acepta `language_code`). Riesgo: turbo a veces da "internal error" del backend. **Recomendado: dejarlo `null` salvo necesidad puntual.** |
| `voice_preset` | string \| null | ID exacto (slug) o label legible de un preset registrado en la pantalla `Presets` | `null` | Si `null` → voice default del `Settings`. Lookup: primero por id, después por label case-insensitive. NO es un voice_id de Kie directamente: es el ID del preset que envuelve un voice_id + voice_settings. |
| `i2v_duration_seconds` | int \| null | `3`, `4`, `5`, `6`, `7`, `8`, `9`, `10`, `11`, `12`, `13`, `14`, `15`, `null` | `null` | Override global. Si seteado FORZA esa duración a TODOS los b-roll del workflow (sobreescribe `step.duration_seconds`). Si `null` → cada step usa su propio valor o el default global (5s). Soportado por Kling 3.0. |
| `scene_approval_mode` | string | `"auto"`, `"manual"` | `"auto"` | `manual` pausa el workflow cuando un b-roll que genera escena nueva (`change_scene=true` **o** `include_product=true`) crea la scene_image; el step queda en `awaiting_approval` y el workflow en `awaiting_approval`. Desde la pantalla Automatización, botón "Revisar aprobación" abre el modal con Aprobar / Regenerar (gasta otra Nano Banana) / Cancelar step / Cerrar. `auto` (default) continúa sin pausa. Solo aplica a b-roll; los a-roll nunca pausan. |
| `promote_product` | bool | `true`, `false` | `false` | Si `true`, el workflow promociona UN producto global. Al encolar, la UI te pide elegir una imagen de producto desde `inputs/` y la sube a Kie (TTL 24h). Los steps con `include_product=true` la componen sobre la modelo con Nano Banana 2. **No pongas la imagen en el JSON**: solo el flag; la foto se elige en la UI. |
| `image_aspect_ratio` | string \| null | `"auto"`, `"1:1"`, `"2:3"`, `"3:2"`, `"3:4"`, `"4:3"`, `"4:5"`, `"5:4"`, `"9:16"`, `"16:9"`, `"21:9"`, `"1:4"` | `"auto"` | Aspect ratio global para todas las imágenes generadas por Nano Banana 2 (base de la modelo y scene_images de cada step). Si se omite, usa `auto` (adapta según las referencias). |
| `model_creation` | object | (3 shapes según `method`) | required | Ver §3. |

### Sobre `audio_language`

Modelos TTS de Kie soportados:
| Modelo | Acepta `language_code` | Estable | Cuándo se usa |
|---|---|---|---|
| `elevenlabs/text-to-speech-multilingual-v2` | NO (devuelve 422) | ✅ Sí | Default cuando `audio_language=null` |
| `elevenlabs/text-to-speech-turbo-2-5` | SÍ (required ISO 639-1) | ⚠️ A veces "internal error" backend | Forzado cuando `audio_language!=null` o preset tiene `language_code` |

---

## 3. `model_creation` — cómo obtener la imagen base del modelo

3 métodos mutuamente excluyentes. Cada uno define SOLO sus campos relevantes:

### 3.1. `method: "prompt"` — genera con Nano Banana 2 (gasta 1 crédito)

```jsonc
{
  "method": "prompt",
  "prompt": "<string max 5000 chars; descripción de la modelo>",
  "reference_image": null    // reservado, no implementado
}
```

| Campo | Tipo | Notas |
|---|---|---|
| `method` | string | Debe ser `"prompt"` |
| `prompt` | string | Requerido, no vacío, máx 5000 chars (siguiendo las directrices del proyecto) |
| `reference_image` | null | Reservado para futuro (sub-prompt con imagen) |

Cuando encolás: se abre el modal `PreviewBaseImageScreen` donde podés
ajustar `aspect_ratio`, `resolution`, `output_format` y aprobar antes de
gastar más Nano Banana en regenerar.

### 3.2. `method: "local"` — sube una foto desde tu disco (0 generación, upload con TTL 24h)

```jsonc
{
  "method": "local",
  "local_path": ""    // dejá vacío "", la UI te abre file picker al encolar
}
```

| Campo | Tipo | Notas |
|---|---|---|
| `method` | string | Debe ser `"local"` |
| `local_path` | string | Vacío `""` → al encolar se abre file picker (recomendado). Path absoluto → se valida que exista y sea jpeg/png ≤ 10MB. |

### 3.3. `method: "catalog"` — reusa una imagen YA registrada en tu DB local (0 generación)

```jsonc
{
  "method": "catalog",
  "asset_kind": "<uploaded | generated>",
  "asset_id": "<string id de la imagen en tu DB local>"
}
```

| Campo | Tipo | Valores | Notas |
|---|---|---|---|
| `method` | string | `"catalog"` | |
| `asset_kind` | string | `"generated"` (Nano Banana, TTL 14d) o `"uploaded"` (subidas, TTL 24h) | Indica qué tabla del store consultar |
| `asset_id` | string | ID interno (ej. `"img_20260606_233843_c1121e"`) | **NO es path de carpeta ni nombre de archivo**. Es el ID que ves en la pantalla `Imágenes` (tecla `I`) o consultando `data/jobs.db` con SQL: `SELECT id, label FROM generated_images;` |

---

## 4. `run[]` — array de steps (escenas)

```jsonc
{
  "run": [
    {
      "step": <int >= 1, consecutivo desde 1>,
      "scene_name": "<string, nombre humano de la escena>",
      "type": "<a-roll | b-roll>",
      "change_scene": <bool>,
      "scene_description": "<string, descripción del nuevo entorno>",
      "prompt": "<string, acción a animar>",
      "text": "<string, lo que dice la modelo>",
      "duration_seconds": <entero 3-15 | null>
    }
  ]
}
```

Reglas globales del array:
- **Min 1 step**.
- **`step` debe ser consecutivo desde 1**: `1, 2, 3, ...`. Sin gaps, sin duplicados.
- Los steps se ejecutan **en serie** (NO en paralelo entre steps).

### 4.1. Campos comunes a `a-roll` y `b-roll`

| Campo | Tipo | Valores válidos | Notas |
|---|---|---|---|
| `step` | int | `>= 1`, consecutivo desde 1 | Identificador secuencial |
| `scene_name` | string | No vacío | Slug auto-generado para el folder de outputs |
| `type` | string | `"a-roll"` o `"b-roll"` | Discriminador principal |
| `change_scene` | bool | `true` / `false` | Default `true`. Si `true`, gasta 1 Nano Banana extra para refit del entorno. **Alias legacy aceptado**: `change_background` (deprecado; usá `change_scene` en workflows nuevos). |
| `scene_description` | string | Cualquier texto, puede vacío | Default `""`. Solo se usa si `change_scene=true`. Si `change_scene=true` Y vacío, warning del validator (el scene se genera solo con `prompt`). **Alias legacy aceptado**: `background_description` (deprecado). |
| `prompt` | string | No vacío, max chars según `type` (ver 4.2 / 4.3) | Describe la acción/escena |
| `text` | string | Reglas según `type` (ver 4.2 / 4.3) | Default `""`. Texto que se manda al TTS |
| `voiceover` | bool | `true`, `false` | Solo aplica a b-roll. Ver 4.3. Default `true`. |
| `include_product` | bool | `true`, `false` | a-roll o b-roll. Default `false`. Si `true`, compone el producto global (requiere `pre_settings.promote_product=true`) sobre la modelo con Nano Banana 2. Ver 4.4. |
| `include_model` | bool | `true`, `false` | a-roll o b-roll. Default `true`. Si `false`, no incluye la foto de la modelo base como referencia (evita que se mezcle su cara/cuerpo en b-rolls que son ilustraciones o macro shots de objeto, ver §4.4). |
| `product_prompt` | string | Cualquier texto, puede vacío | Default `""`. Solo se usa si `include_product=true`. Se añade al prompt de la escena para indicar cómo/dónde poner el producto. |
| `image_aspect_ratio` | string \| null | Mismos que en `pre_settings` | Default `null`. Sobrescribe el aspect ratio global del workflow para este step puntualmente (ej: forzar `9:16` en el step 5). |

> **Regla de generación de escena (Nano Banana)**: un step genera una
> scene_image nueva con Nano Banana 2 si `change_scene=true` **O**
> `include_product=true`. Si ninguno, reusa la imagen base tal cual (no
> gasta Nano Banana). Combinaciones:
> - `change_scene=true` + `include_product=false` → cambia el entorno (refs: solo base).
> - `change_scene=false` + `include_product=true` → mantiene el fondo de la base, solo añade el producto (refs: base + producto).
> - `change_scene=true` + `include_product=true` → cambia el entorno **y** añade el producto (refs: base + producto).
> - `change_scene=false` + `include_product=false` → reusa la base (sin Nano Banana).

> **Campos requeridos vs opcionales**: el único set obligatorio por step es
> `step`, `scene_name`, `type`, `prompt`. Los demás (`change_scene`,
> `scene_description`, `text`, `duration_seconds`, `voiceover`) tienen
> defaults y podés omitirlos. **Excepción**: a-roll exige `text` no vacío.

### 4.2. `type: "a-roll"` (la modelo habla a cámara, lip-sync)

**Operaciones Kie disparadas**:
- Scene image: si `change_scene=true`, **1 Nano Banana** (refit del fondo). Si `false`, reusa la imagen base.
- **1 TTS** del `text` (siempre).
- **1 Avatar Pro** (Kling) — lip-sync video + audio embebido.

**Restricciones específicas**:
| Campo | Restricción a-roll |
|---|---|
| `text` | **Obligatorio, no vacío**. Es lo que la modelo va a decir. Si vacío → error de validación. Max 5000 chars. |
| `prompt` | Max **5000 chars**. Describe gesto/expresión (Avatar Pro lo usa como hint visual). |
| `duration_seconds` | **Ignorado** (warning del validator). La duración del a-roll = duración del audio TTS. |

**Output del step**:
```
outputs/wf_<id>/step_NN_<scene_slug>/
├── scene.png            (si change_scene=true; sino, copia de base.png)
└── final.mp4            (lip-sync con audio embebido)
```

### 4.3. `type: "b-roll"` (escena auxiliar sin lip-sync, Kling 3.0)

Usamos el modelo `kling-3.0/video` (no el 2.6 viejo). Soporta duraciones
3-15s, modos std/pro/4K, aspect ratios 16:9/9:16/1:1, y sound effects
ambientales generados nativamente.

> **mode y aspect_ratio NO son configurables desde el JSON** (todavía). Todo
> b-roll usa `mode=pro` (1080p) y `aspect_ratio=16:9` por defecto; Kling
> auto-adapta el ratio a la imagen scene de referencia. No agregues campos
> `mode` ni `aspect_ratio` al step: el schema los rechaza.

**Operaciones Kie disparadas** (dependen de `voiceover` + `text`):

| `voiceover` | `text` | Operaciones | Output |
|---|---|---|---|
| `true` (default) | no vacío | scene_image (si change_scene) + 1 TTS + 1 Kling i2v silencioso | `scene.png` + `video.mp4` (silencioso) + `audio.mp3` aparte |
| `true` (default) | vacío `""` | scene_image (si change_scene) + 1 Kling i2v silencioso | `scene.png` + `video.mp4` (silencioso) |
| `false` | (ignorado, warning) | scene_image (si change_scene) + 1 Kling i2v **con sound efx nativos** | `scene.png` + `video.mp4` (con sound efx embebidos) |

**Sobre `voiceover: false`**:
- Kling 3.0 genera sound effects ambientales (NO voz hablada) basados en el `prompt` del step.
- Es útil para escenas atmosféricas (mar, viento, fuego, ambient de un café, etc).
- NO sirve si querés voz humana (eso es `voiceover: true` con `text`).
- Si el `text` está seteado con `voiceover: false`, se ignora (warning del validator).

**Restricciones específicas**:
| Campo | Restricción b-roll |
|---|---|
| `text` | Si `voiceover=true`: opcional (vacío = silencioso, no-vacío = audio.mp3 aparte). Si `voiceover=false`: se ignora. Max 5000 chars. |
| `prompt` | Max **2500 chars** (límite Kling). |
| `voiceover` | bool. Default `true` (audio TTS aparte). `false` → sound efx nativos de Kling embebidos. |
| `duration_seconds` | Opcional. Cualquier entero **3-15**. Si `null` → usa `pre_settings.i2v_duration_seconds` global o `5` default. |
| `change_scene=false` | Warning del validator: "normalmente querés `true` para escenas auxiliares". |

**Output del step según ruta**:
- `voiceover=true` + `text` no vacío:
  ```
  outputs/wf_<id>/step_NN_<scene_slug>/
  ├── scene.png
  ├── video.mp4         (silencioso, 3-15s)
  └── audio.mp3         (TTS aparte para montar en post)
  ```
- `voiceover=true` + `text` vacío:
  ```
  outputs/wf_<id>/step_NN_<scene_slug>/
  ├── scene.png
  └── video.mp4         (silencioso)
  ```
- `voiceover=false`:
  ```
  outputs/wf_<id>/step_NN_<scene_slug>/
  ├── scene.png
  └── video.mp4         (con sound efx ambient embebidos)
  ```

### 4.4. Producto promocional (`include_product` + `product_prompt`)

Un workflow puede promocionar **un producto global**. El flujo:

1. En `pre_settings` poné `"promote_product": true`.
2. Al encolar, la UI te abre un file picker para elegir la foto del
   producto desde `inputs/`. Se sube a Kie (TTL 24h). **No pongas la
   imagen en el JSON** — solo el flag `promote_product`.
3. En cada step (a-roll **o** b-roll) que deba mostrar el producto, poné
   `"include_product": true` y un `"product_prompt"` describiendo cómo/dónde
   colocarlo.

Cuando un step tiene `include_product=true`, Nano Banana 2 recibe **dos
imágenes de referencia** (`[base_de_la_modelo, producto]`) y compone el
producto sobre la modelo. El resultado (scene.png) se usa como input del
render (Avatar Pro para a-roll, Kling 3.0 para b-roll).

| Campo | Restricción |
|---|---|
| `include_product` | bool. Default `false`. Requiere `pre_settings.promote_product=true` (sino error de validación). Aplica a a-roll y b-roll. |
| `product_prompt` | Cualquier texto. Solo se usa si `include_product=true`. Vacío = warning (Nano Banana compone el producto solo con el prompt de la escena). |

**Interacción con `change_scene`** (ver la regla en §4.1):
- `change_scene=false` + `include_product=true` → Nano Banana mantiene el
  fondo de la base y solo añade el producto (misma modelo, mismo fondo, con
  producto).
- `change_scene=true` + `include_product=true` → Nano Banana cambia el
  entorno (según `scene_description`) **y** añade el producto.

**Aprobación humana** (`scene_approval_mode=manual`): solo los **b-roll**
que generan escena pausan para aprobación. Un a-roll con producto genera la
escena pero **nunca** pausa.

---

## 5. Tabla de presupuesto Kie

Calculá total = base + scene_images + tts + videos.

| Componente | Cuándo se dispara | Cantidad |
|---|---|---|
| Nano Banana 2 (base del modelo) | `model_creation.method == "prompt"` | 1 (0 si method=local o catalog) |
| Nano Banana 2 (scene image) | step con `change_scene=true` **o** `include_product=true` | 1 por step que genere escena (se dispara una sola vez por step aunque tenga ambos flags) |
| TTS ElevenLabs | a-roll siempre + b-roll con `text` no vacío | 1 por evento |
| Avatar Pro (Kling lip-sync) | cada step `type=a-roll` | 1 por a-roll |
| Kling i2v (b-roll, `kling-3.0/video`) | cada step `type=b-roll` | 1 por b-roll. Costo varía según `mode` (std/pro/4K). |

**Ejemplo de cálculo**:
Workflow con `method=catalog` + 1 a-roll (`change_scene=true`, `text="x"`) + 1 b-roll (`change_scene=true`, `text="y"`, `duration=10`):
- Nano Banana base: 0 (catalog reusa)
- Nano Banana scene: 2 (1 por step)
- TTS: 2 (1 a-roll + 1 b-roll con text)
- Avatar Pro: 1
- Kling i2v: 1
- **Total: 6 llamadas Kie**

---

## 6. Voices builtin de Kie

`voice_preset` apunta a un preset registrado en `Presets` (tu lista local), no
directamente a una de estas voces. El preset envuelve uno de estos `voice_id`
+ voice_settings opcionales. Esta lista es para crear nuevos presets.

| voice_id | label | description |
|---|---|---|
| `EkK5I93UQWFDigLMpZcX` | James | Husky, Engaging and Bold |
| `Z3R5wn05IrDiVCyEkUrK` | Arabella | Mysterious and Emotive |
| `NNl6r8mD7vthiJatiJt1` | Bradford | Expressive and Articulate |
| `YOq2y2Up4RgXP2HyXjE5` | Xavier | Dominating, Metallic Announcer |
| `B8gJV1IhpuegLxdpXFOE` | Kuon | Cheerful, Clear and Steady |
| `2zRM7PkgwBPiau2jvVXc` | Monika Sogam | Deep and Natural |
| `1SM7GgM6IMuvQlz2BwM3` | Mark | Casual, Relaxed and Light |
| `5l5f8iK3YPeGga21rQIX` | Adeline | Feminine and Conversational |
| `scOwDtmlUjD3prqpp97I` | Sam | Support Agent |
| `NOpBlnGInO9m6vDvFkFC` | Spuds Oxley | Wise and Approachable |
| `BZgkqPqms7Kj9ulSkVzn` | Eve | Authentic, Energetic and Happy |
| `wo6udizrrtpIxWGp2qJk` | Northern Terry | — |
| `gU0LNdkMOQCOrPrwtbee` | British Football Announcer | — |
| `DGzg6RaUqxGRTHSBjfgF` | Brock | Commanding and Loud Sergeant |
| `x70vRnQBMBu4FAYhjJbO` | Nathan | Virtual Radio Host |
| `Sm1seazb4gs7RSlUVw7c` | Anika | Animated, Friendly and Engaging |
| `P1bg08DkjqiVEzOn76yG` | Viraj | Rich and Soft |
| `qDuRKMlYmrm8trt5QyBn` | Taksh | Calm, Serious and Smooth |
| `qXpMhyvQqiRxWQs4qSSB` | Horatius | Energetic Character Voice |
| `TX3LPaxmHKxFdv7VOQHJ` | Liam | Energetic, Social Media Creator |
| `N2lVS1w4EtoT3dr4eOWO` | Callum | Husky Trickster |
| `FGY2WhTYpPnrIDTdsKH5` | Laura | Enthusiast, Quirky Attitude |
| `kPzsL2i3teMYv0FxEYQ6` | Brittney | Social Media Voice - Fun, Youthful & Informative |
| `UgBBYS2sOqTuMpoF3BR0` | Mark (alt) | Natural Conversations |
| `hpp4J3VqNfWAUOO0d1Us` | Bella | Professional, Bright, Warm |
| `nPczCjzI2devNBz1zQrb` | Brian | Deep, Resonant and Comforting |
| `uYXf8XasLslADfZ2MB4u` | Hope | Bubbly, Gossipy and Girly |
| `gs0tAILXbY5DNrJrsM6F` | Jeff | Classy, Resonating and Strong |
| `DTKMou8ccj1ZaWGBiotd` | Jamahal | Young, Vibrant, and Natural |
| `vBKc2FfBKJfcZNyEt1n6` | Finn | Youthful, Eager and Energetic |
| `DYkrAHD8iwork3YSUBbs` | Tom | Conversations & Books |
| `56AoDkrOh6qfVPDXZ7Pt` | Cassidy | Crisp, Direct and Clear |
| `eR40ATw9ArzDf9h3v7t7` | Addison 2.0 | Australian Audiobook & Podcast |
| `g6xIsTj2HwM6VR4iXFCw` | Jessica Anne Bogart | Chatty and Friendly |
| `lcMyyd2HUfFzxdCaC4Ta` | Lucy | Fresh & Casual |
| `6aDn1KB0hjpdcocrUkmq` | Tiffany | Natural and Welcoming |
| `Sq93GQT4X1lKDXsQcixO` | Felix | Warm, Positive & Contemporary RP |
| `flHkNRp1BlvT73UL6gyz` | Jessica Anne Bogart (alt) | Eloquent Villain |
| `9yzdeviXkFddZ4Oz8Mok` | Lutz | Chuckling, Giggly and Cheerful |
| `pPdl9cQBQq4p6mRkZy2Z` | Emma | Adorable and Upbeat |
| `zYcjlYFOd3taleS0gkk3` | Edward | Loud, Confident and Cocky |
| `nzeAacJi50IvxcyDnMXa` | Marshal | Friendly, Funny Professor |
| `ruirxsoakN0GWmGNIo04` | John Morgan | Gritty, Rugged Cowboy |
| `TC0Zp7WVFzhA8zpTlRqV` | Aria | Sultry Villain |
| `ljo9gAlSqKOvF6D8sOsX` | Viking Bjorn | Epic Medieval Raider |
| `PPzYpIqttlTYA83688JI` | Pirate Marshal | — |
| `8JVbfL6oEdmuxKn5DK2C` | Johnny Kid | Serious and Calm Narrator |
| `iCrDUkL56s3C8sCRl7wb` | Hope (alt) | Poetic, Romantic and Captivating |
| `wJqPPQ618aTW29mptyoc` | Ana Rita | Smooth, Expressive and Bright |
| `EiNlNiXeDU1pqqOPrYMO` | John Doe | Deep |
| `4YYIPFl9wE5c4L2eu2Gb` | Burt Reynolds™ | Deep, Smooth and Clear |
| `6F5Zhi321D3Oq7v1oNT4` | Hank | Deep and Engaging Narrator |
| `YXpFCvM1S3JbWEJhoskW` | Wyatt | Wise Rustic Cowboy |
| `LG95yZDEHg6fCZdQjLqj` | Phil | Explosive, Passionate Announcer |
| `CeNX9CMwmxDxUF5Q2Inm` | Johnny Dynamite | Vintage Radio DJ |
| `aD6riP1btT197c6dACmy` | Rachel M | Pro British Radio Presenter |
| `mtrellq69YZsNwzUSyXh` | Rex Thunder | Deep N Tough |
| `dHd5gvgSOzSfduK4CvEg` | Ed | Late Night Announcer |
| `eVItLK1UvXctxuaRV2Oq` | Jean | Alluring and Playful Femme Fatale |
| `esy0r39YPLQjOczyOib8` | Britney | Calm and Calculative Villain |
| `Tsns2HvNFKfGiNjllgqo` | Sven | Emotional and Nice |
| `1U02n4nD6AdIZ9CjF053` | Viraj (alt) | Smooth and Gentle |
| `AeRdCCKzvd23BpJoofzx` | Nathaniel | Engaging, British and Calm |
| `LruHrtVF6PSyGItzMNHS` | Benjamin | Deep, Warm, Calming |
| `1wGbFxmAM3Fgw63G1zZJ` | Allison | Calm, Soothing and Meditative |
| `hqfrgApggtO1785R4Fsn` | Theodore HQ | Serene and Grounded |
| `MJ0RnG71ty4LH3dvNfSd` | Leon | Soothing and Grounded |

---

## 7. Ejemplos completos

### 7.1. Workflow mínimo — 1 a-roll, imagen base con prompt

```json
{
  "workflow": "Minimal A-Roll Demo",
  "pre_settings": {
    "audio_language": null,
    "voice_preset": "demo_1",
    "model_creation": {
      "method": "prompt",
      "prompt": "Photorealistic medium close-up portrait of a 30-year-old Latin woman with natural face, dark hair, neutral cream t-shirt, plain warm beige background, soft natural window light, documentary style, 35mm lens, raw and authentic. --ar 16:9"
    }
  },
  "run": [
    {
      "step": 1,
      "scene_name": "Hook",
      "type": "a-roll",
      "change_scene": false,
      "scene_description": "",
      "prompt": "Looking at the camera, calm and natural expression, subtle nod.",
      "text": "Hola, esto es una prueba rápida."
    }
  ]
}
```

Total Kie: 3 llamadas (1 Nano Banana base + 1 TTS + 1 Avatar Pro).

### 7.2. Workflow medio — 1 a-roll + 1 b-roll con voiceover, reusando imagen del catálogo

```json
{
  "workflow": "A-Roll + B-Roll Demo",
  "pre_settings": {
    "audio_language": null,
    "voice_preset": "demo_2",
    "model_creation": {
      "method": "catalog",
      "asset_kind": "generated",
      "asset_id": "img_20260606_233843_c1121e"
    }
  },
  "run": [
    {
      "step": 1,
      "scene_name": "Hook A-Roll",
      "type": "a-roll",
      "change_scene": false,
      "scene_description": "",
      "prompt": "Looking at the camera, calm expression, subtle nod.",
      "text": "Mira esto, te va a interesar."
    },
    {
      "step": 2,
      "scene_name": "B-Roll With Voiceover",
      "type": "b-roll",
      "change_scene": true,
      "scene_description": "Warm cozy living room corner with wooden side table, steaming ceramic coffee mug, open hardcover book, soft afternoon window light, no people visible.",
      "prompt": "Slow cinematic dolly-in over the wooden table, steam rising softly from the coffee mug, pages fluttering gently.",
      "text": "Y por eso te conviene escucharme hasta el final.",
      "voiceover": true,
      "duration_seconds": 5
    }
  ]
}
```

Total Kie: 5 llamadas (0 Nano Banana base + 1 Nano Banana scene + 2 TTS + 1 Avatar Pro + 1 Kling i2v).

### 7.2b. Workflow b-roll con sound effects nativos (Kling 3.0 `sound=true`)

```json
{
  "workflow": "Ambient B-Roll Demo",
  "pre_settings": {
    "audio_language": null,
    "voice_preset": null,
    "model_creation": {
      "method": "catalog",
      "asset_kind": "generated",
      "asset_id": "img_20260606_233843_c1121e"
    }
  },
  "run": [
    {
      "step": 1,
      "scene_name": "Ocean Waves Ambient",
      "type": "b-roll",
      "change_scene": true,
      "scene_description": "Wide aerial shot of crashing ocean waves on a rocky beach at golden hour, sea foam, deep blue water, cinematic.",
      "prompt": "Slow aerial pan across the crashing waves, water splashing rhythmically, seagulls in the distance, golden sunset light reflecting off the wet rocks.",
      "text": "",
      "voiceover": false,
      "duration_seconds": 10
    }
  ]
}
```

Total Kie: 2 llamadas (0 base + 1 Nano Banana scene + 0 TTS + 1 Kling i2v con sound efx embebidos).
**No hay `audio.mp3` aparte**: el sonido de olas viene dentro del `video.mp4`.

### 7.2c. Workflow con producto promocional (`promote_product` + `include_product`)

Ver `workflows/example_product_promo.json`. Promociona un producto global
(elegido en la UI desde `inputs/`), incluido en un a-roll y un b-roll:

```json
{
  "workflow": "Product Promo Demo",
  "pre_settings": {
    "audio_language": null,
    "voice_preset": "demo_promo",
    "scene_approval_mode": "auto",
    "promote_product": true,
    "model_creation": {
      "method": "catalog",
      "asset_kind": "generated",
      "asset_id": "img_20260606_233843_c1121e"
    }
  },
  "run": [
    {
      "step": 1,
      "scene_name": "Hook A-Roll con producto",
      "type": "a-roll",
      "change_scene": false,
      "prompt": "Looking at the camera, holding the product naturally at chest height.",
      "text": "Te voy a mostrar el producto que me cambió la rutina.",
      "include_product": true,
      "product_prompt": "Holds the amber jar in her right hand at chest height, label facing the camera."
    },
    {
      "step": 2,
      "scene_name": "B-Roll producto en escena nueva",
      "type": "b-roll",
      "change_scene": true,
      "scene_description": "Clean linen surface, soft window light, warm apothecary aesthetics.",
      "prompt": "Slow cinematic push-in, soft window light catching the product label.",
      "text": "Mirá los detalles: ingredientes simples.",
      "include_product": true,
      "product_prompt": "The amber jar rests centered on the linen surface, label fully visible.",
      "duration_seconds": 5
    }
  ]
}
```

Total Kie: 5 llamadas (0 base catalog + 2 Nano Banana scene/producto + 2 TTS
+ 1 Avatar Pro + 1 Kling i2v). Nota: el a-roll genera scene (por
`include_product`) aunque `change_scene=false`; mantiene el fondo de la base.
**Importante**: la foto del producto NO va en el JSON — la elegís en la UI
al encolar (file picker desde `inputs/`).

### 7.3. Workflow grande — múltiples steps, sube modelo desde local

```json
{
  "workflow": "Local Model Full Pipeline",
  "pre_settings": {
    "audio_language": null,
    "voice_preset": "latina_warm_authentic",
    "i2v_duration_seconds": 10,
    "model_creation": {
      "method": "local",
      "local_path": ""
    }
  },
  "run": [
    {
      "step": 1,
      "scene_name": "Hook",
      "type": "a-roll",
      "change_scene": false,
      "scene_description": "",
      "prompt": "Direct eye contact, controlled intensity, like sharing a secret.",
      "text": "Tienes que escuchar esto antes de que sea tarde."
    },
    {
      "step": 2,
      "scene_name": "B-Roll Object",
      "type": "b-roll",
      "change_scene": true,
      "scene_description": "Overhead shot of a wooden desk with an open notebook, vintage pen, brass desk lamp turned on, scattered receipts, cinematic warm lighting.",
      "prompt": "Slow overhead pan across the desk, soft camera drift, lamp light flickering subtly, notebook pages gently turning.",
      "text": ""
    },
    {
      "step": 3,
      "scene_name": "Reinforcement A-Roll",
      "type": "a-roll",
      "change_scene": true,
      "scene_description": "Cozy reading nook with a beige armchair, plants, soft afternoon light through linen curtains.",
      "prompt": "Sitting calmly, warm smile, hands resting, looking at the camera with conviction.",
      "text": "Esto te va a cambiar la forma de pensar."
    }
  ]
}
```

Total Kie: 7 llamadas (0 base + 2 scene + 2 TTS + 2 Avatar Pro + 1 Kling i2v).

---

## 8. Cheatsheet de errores comunes

| Error de validación | Causa | Solución |
|---|---|---|
| `workflow.name vacío` | El campo `"workflow"` falta o está vacío | Agregar nombre humano |
| `workflow debe tener al menos 1 step` | `"run": []` | Agregar al menos 1 step |
| `step #N: scene_name vacío` | Step sin `scene_name` | Agregar |
| `step N: prompt vacío` | Step sin `prompt` | Agregar |
| `step N: tipo a-roll requiere 'text' no vacío` | a-roll con `text=""` | Agregar texto o cambiar a `type: "b-roll"` |
| `step N: duration i2v inválido: X` | `duration_seconds` fuera de 3-15 | Usar un entero entre 3 y 15, o null |
| `step N: a-roll trae duration_seconds` | a-roll con duration (warning) | Sacar el campo (se ignora) |
| `model_creation.method='prompt' requiere prompt no vacío` | method=prompt sin `prompt` | Agregar prompt |
| `model_creation.method='catalog' requiere asset_kind y asset_id` | Faltan campos | Agregar ambos |
| `steps no consecutivos` | step jumps (1, 3, 4) | Renumerar consecutivamente desde 1 |
| `steps [N] tienen include_product=true pero pre_settings.promote_product=false` | Step pide producto pero el workflow no lo promociona | Poné `promote_product: true` o quitá `include_product` |
| Audio task fallido "internal error" | Backend de Kie tuvo problema con turbo | Reintentar o setear `audio_language: null` |

---

## 9. Bloque listo para pegar al prompt de la IA

> Querés generar un workflow JSON compatible con Kie Avatar Studio. Las reglas
> están en este documento. Genera SOLO el JSON pedido, sin markdown wrappers ni
> texto extra. Respeta:
> - `pre_settings.model_creation` con shape exacto según `method`.
> - `step.step` consecutivo desde 1.
> - a-roll: `text` obligatorio no vacío.
> - b-roll: `duration_seconds` entero 3-15 o null.
> - prompts no superan los límites de chars (5000 a-roll, 2500 b-roll, 5000 base).
> - producto: si el guion promociona un producto, poné `pre_settings.promote_product: true`
>   y en cada step que lo muestre `include_product: true` + un `product_prompt` (la foto
>   del producto la elige el usuario en la UI, NO va en el JSON).
> - `voice_preset` debe ser el id o label de un preset que el usuario ya tenga
>   registrado (preguntale si no sabés cuál usar).
> - `audio_language: null` salvo que el usuario pida explícitamente otro idioma
>   (es más estable).

---

## 10. JSON anotado con TODOS los valores soportados (para pegar a una IA)

Este es un mapa de referencia: cada valor describe el tipo, los valores
aceptados, el default y notas. **No es un workflow ejecutable** — sirve para
que una IA entienda qué puede poner en cada propiedad. Para `model_creation`
elegí UNO de los 3 bloques (`prompt` / `local` / `catalog`).

```jsonc
{
  "workflow": "<string requerido, 1-200 chars · nombre humano del workflow>",
  "pre_settings": {
    "audio_language": "<string ISO 639-1 (es, en, es-419, pt, fr, de, it, ja, ko, zh, ar, hi, ...) | null · default null · null=multilingual-v2 estable; seteado=turbo-2-5>",
    "voice_preset": "<string id o label de un preset registrado en pantalla Presets | null · default null>",
    "i2v_duration_seconds": "<int 3..15 | null · default null · FORZA esa duración en TODOS los b-roll si se setea>",
    "scene_approval_mode": "<\"auto\" | \"manual\" · default \"auto\" · manual pausa cada b-roll que genera scene nueva para aprobación humana>",
    "promote_product": "<true | false · default false · true => la UI pide elegir la foto del producto desde inputs/ al encolar; NO pongas la imagen en el JSON>",
    "image_aspect_ratio": "<string \"auto\" | \"1:1\" | \"9:16\" | \"16:9\" | ... · default \"auto\" · aspect ratio global para todas las imágenes generadas por Nano Banana 2>",
    "model_creation": {
      "method": "prompt",
      "prompt": "<string 1-5000 chars · descripción de la modelo (estilo, edad, vestimenta, fondo, lighting, cámara) · SOLO si method=prompt>",

      "// método 2 (local)": "method=\"local\" + local_path=\"<path absoluto a .jpg/.png <=10MB | \\\"\\\" para que la UI abra file picker>\"",
      "// método 3 (catalog)": "method=\"catalog\" + asset_kind=\"<uploaded | generated>\" + asset_id=\"<id interno de la DB, ej. img_20260606_233843_c1121e>\""
    }
  },
  "run": [
    {
      "step": "<int >=1, consecutivo desde 1 sin gaps ni duplicados>",
      "scene_name": "<string requerido, 1-200 chars · se slugifica para el folder de outputs>",
      "type": "<\"a-roll\" | \"b-roll\" · a-roll=modelo habla a cámara (lip-sync, Avatar Pro); b-roll=escena auxiliar (Kling 3.0 video)>",
      "change_scene": "<true | false · default true · true=genera scene.png nueva con Nano Banana usando base + scene_description; false=reusa la base>",
      "scene_description": "<string · default \"\" · entorno para Nano Banana, solo se usa si change_scene=true>",
      "prompt": "<string requerido no vacío · acción/escena a animar · max 5000 a-roll, 2500 b-roll>",
      "text": "<string · default \"\" · max 5000 · a-roll: OBLIGATORIO no vacío; b-roll: opcional (con voiceover=true genera audio.mp3 aparte; vacío=silencioso)>",
      "duration_seconds": "<int 3..15 | null · default null · SOLO b-roll; a-roll lo ignora>",
      "voiceover": "<true | false · default true · SOLO b-roll · true=TTS aparte + video silencioso; false=Kling genera sound effects nativos y se ignora text>",
      "include_product": "<true | false · default false · a-roll o b-roll · true=compone el producto global sobre la modelo (requiere promote_product=true); dispara Nano Banana aunque change_scene=false>",
      "include_model": "<true | false · default true · true=pasa foto de la modelo base como referencia; false=no la pasa (para b-rolls de objeto/ilustración puro donde no debe aparecer la modelo)>",
      "product_prompt": "<string · default \"\" · SOLO si include_product=true · cómo/dónde colocar el producto (ej. 'sostiene el frasco ámbar en su mano derecha a la altura del pecho')>",
      "image_aspect_ratio": "<string \"auto\" | \"1:1\" | \"9:16\" | \"16:9\" | ... | null · default null · sobrescribe el aspect ratio global del workflow para este step puntualmente>"
    }
  ]
}
```

**Reglas que la IA debe respetar** (resumen):
- `model_creation`: UN solo método (`prompt` | `local` | `catalog`) con su shape exacto.
- `step.step` consecutivo desde 1.
- a-roll: `text` obligatorio no vacío; a-roll ignora `duration_seconds`, `voiceover`.
- b-roll: `duration_seconds` entero 3-15 o null.
- Nano Banana se invoca si `change_scene=true` **o** `include_product=true`.
- `include_product=true` exige `pre_settings.promote_product=true`.
- La foto del producto NO va en el JSON (se elige en la UI). El JSON solo trae `promote_product` + `include_product` + `product_prompt`.
- `mode` y `aspect_ratio` del b-roll NO son configurables desde el JSON (fijos `pro` / `16:9`).
- Devolvé SOLO el JSON, sin comentarios `//` ni markdown wrappers.
