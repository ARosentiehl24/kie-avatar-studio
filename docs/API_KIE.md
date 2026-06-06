# Kie.ai – Cheatsheet de APIs usadas

## 1. File Upload

```http
POST https://kieai.redpandaai.co/api/file-stream-upload
Authorization: Bearer <KIE_API_KEY>
Content-Type: multipart/form-data
```

Campos:

```text
file       : binario
uploadPath : images/avatar-models
fileName   : modelo-001.png
```

Curl:

```bash
curl -X POST "https://kieai.redpandaai.co/api/file-stream-upload" \
  -H "Authorization: Bearer $KIE_API_KEY" \
  -F "file=@./modelo.png" \
  -F "uploadPath=images/avatar-models" \
  -F "fileName=modelo-001.png"
```

Respuesta relevante:

```json
{
  "success": true, "code": 200,
  "data": {
    "fileName": "modelo-001.png",
    "filePath": "kieai/.../modelo-001.png",
    "downloadUrl": "https://tempfile.redpandaai.co/.../modelo-001.png",
    "fileSize": 154832,
    "mimeType": "image/png"
  }
}
```

Campo importante: `data.downloadUrl`.

## 2. Crear task TTS (ElevenLabs)

```http
POST https://api.kie.ai/api/v1/jobs/createTask
Authorization: Bearer <KIE_API_KEY>
Content-Type: application/json
```

Body mínimo:

```json
{
  "model": "elevenlabs/text-to-speech-multilingual-v2",
  "input": {
    "text": "Texto del guion (max 5000 chars)",
    "voice": "EkK5I93UQWFDigLMpZcX"
  }
}
```

Body completo con `voice_settings` (todos los campos opcionales):

```json
{
  "model": "elevenlabs/text-to-speech-multilingual-v2",
  "input": {
    "text": "Texto del guion (max 5000 chars)",
    "voice": "EkK5I93UQWFDigLMpZcX",
    "stability": 0.5,
    "similarity_boost": 0.75,
    "style": 0.0,
    "speed": 1.0,
    "language_code": "es"
  }
}
```

**Importante**: los voice_settings van **planos dentro de `input`**, NO
anidados en un sub-objeto `voice_settings`.

Rangos exactos del spec:

| Campo | Tipo | Rango | Default Kie | Notas |
|---|---|---|---|---|
| `text` | string | max 5000 chars | required | — |
| `voice` | string | enum 67 voces o voice_id custom | required | acepta IDs fuera del catálogo (cuentas Pro ElevenLabs) |
| `stability` | number | 0.0 – 1.0 | 0.5 | — |
| `similarity_boost` | number | 0.0 – 1.0 | 0.75 | — |
| `style` | number | 0.0 – 1.0 | 0 | — |
| `speed` | number | 0.7 – 1.2 | 1.0 | — |
| `language_code` | string | ISO 639-1 (2 letras) | "" | **solo turbo v2.5 y flash v2.5** — el multilingual-v2 devuelve 422 |

Modelos TTS disponibles en Kie (mismo body, mismo catálogo de voces):

| Modelo | Latencia | Uso típico |
|---|---|---|
| `elevenlabs/text-to-speech-multilingual-v2` | Media | Calidad estándar (el que usamos por default) |
| `elevenlabs/text-to-speech-turbo-2-5` | Baja | Acepta `language_code` |

Respuesta:

```json
{ "code": 200, "msg": "success", "data": { "taskId": "task_xxx" } }
```

### Catálogo built-in de voces

Kie expone **67 voces curadas** en el `enum` del campo `voice` (compartidas
entre los 2 modelos TTS). El catálogo vive en `domain/kie_voice_catalog.py`
como constante `BUILTIN_VOICES` y debe mantenerse sincronizado a mano cuando
Kie actualice el spec (`docs.kie.ai/market/elevenlabs/text-to-speech-multilingual-v2`).

**Kie NO expone un endpoint para listar voces dinámicamente** (verificado
contra el sitemap completo + Common API). El único listado oficial vive en
la descripción del campo `voice` del OpenAPI YAML.

**Preview de voces**: cualquier `voice_id` (built-in o custom) tiene un MP3
público en:

```text
https://static.aiquickdraw.com/elevenlabs/voice/<voice_id>.mp3
```

### Voice Design (texto → voz)

**No disponible en Kie**. Voice Design por prompt es un feature de la UI
de ElevenLabs (`elevenlabs.io/app/voice-design`); el voice_id resultante se
puede usar después con los endpoints TTS de Kie (acepta voice_ids custom).

Suno expone `voice/generate` para clonar una voz desde audio (no desde
prompt), pero el `voiceId` resultante **solo funciona con los modelos
musicales de Suno**, no con TTS para avatar.

## 3. Crear task Avatar (Kling)

```json
{
  "model": "kling/ai-avatar-pro",
  "input": {
    "image_url": "https://tempfile.redpandaai.co/.../modelo.png",
    "audio_url": "https://.../audio.mp3",
    "prompt": "Mirada a cámara, expresión natural, gestos suaves, tono confiado."
  }
}
```

Restricciones:

```text
imagen      : jpeg/png, max 10 MB
audio       : max 100 MB, max 5 min, formatos mpeg/wav/x-wav/aac/mp4/ogg
prompt      : max 5000 chars
```

## 5. Crear task Nano Banana 2 (Google — generación de imagen)

Mismo endpoint `createTask`, distinto `model` y `input`:

```json
{
  "model": "nano-banana-2",
  "input": {
    "prompt": "Comic poster: cool banana hero in shades …",
    "image_input": [
      "https://tempfile.redpandaai.co/.../ref1.png"
    ],
    "aspect_ratio": "16:9",
    "resolution": "2K",
    "output_format": "png"
  }
}
```

Restricciones del input:

| Campo | Tipo | Rango | Default Kie | Notas |
|---|---|---|---|---|
| `prompt` | string | max **20000 chars** | required | Mucho más generoso que TTS/avatar (5000) |
| `image_input` | array de URLs | max **14** items | `[]` (text-to-image) | URLs públicas; cada archivo jpg/png/webp ≤ 30 MB. Típicamente `downloadUrl` de uploads o `kie_url` de generadas |
| `aspect_ratio` | string enum | `auto, 1:1, 2:3, 3:2, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9, 1:4, 1:8, 4:1, 8:1` | `auto` | El modelo decide cuando es `auto` |
| `resolution` | string enum | `1K, 2K, 4K` | `1K` | — |
| `output_format` | string enum | `jpg, png` | `jpg` | — |

Respuesta y polling: idénticos al resto (`{ "data": { "taskId": "..." } }` + `recordInfo`).

Curl mínimo (text-to-image puro):

```bash
curl -X POST "https://api.kie.ai/api/v1/jobs/createTask" \
  -H "Authorization: Bearer $KIE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "nano-banana-2",
    "input": {
      "prompt": "un atardecer con palmeras, estilo polaroid",
      "image_input": [],
      "aspect_ratio": "auto",
      "resolution": "1K",
      "output_format": "jpg"
    }
  }'
```

Implementado en `KieClient.create_nano_banana_task(...)` (`infra/kie_client.py`).
La validación de enums + prompt + refs vive en `domain/policies.py`
(`validate_image_prompt`, `validate_image_settings`, `validate_image_refs`);
el cliente HTTP no valida nada (CR-2.1).

## 6. Crear task Kling 2.6 image-to-video (b-roll silencioso)

Mismo endpoint `createTask`, modelo `kling-2.6/image-to-video`. Genera un
video silencioso a partir de una imagen estática + prompt. Usado en el
subsistema de **automatización** para los steps `type=b-roll`: el video
no lleva audio sincronizado (a diferencia de Avatar Pro), así que si el
step trae `text` no vacío, el audio TTS se descarga aparte para que el
usuario lo monte en post-producción.

```json
{
  "model": "kling-2.6/image-to-video",
  "input": {
    "image_url": "https://tempfile.redpandaai.co/.../scene.png",
    "prompt": "Hands struggling to button jeans, cinematic close-up, raw natural light",
    "duration": 5
  }
}
```

Restricciones del input:

| Campo | Tipo | Rango | Default Kie | Notas |
|---|---|---|---|---|
| `image_url` | string URL | http(s) público | required | URL de imagen estática hosteada en Kie (típicamente `kie_url` de un `GeneratedImage` o de un `UploadedImage`) |
| `prompt` | string | max **2500 chars** | required | Describe la acción a animar |
| `duration` | int enum | `5` o `10` | `5` | Duración del clip en segundos |

Respuesta y polling: idénticos al resto (`{ "data": { "taskId": "..." } }` + `recordInfo`).

Costos (referencia, sujetos a cambio): ~$0.28 por video de 5s, ~$0.56
por 10s. Validá en https://kie.ai/billing.

Implementado en `KieClient.create_image_to_video_task(...)` (`infra/kie_client.py`).
La validación de `duration` vive en `domain/policies.py:validate_i2v_duration`.
El cliente HTTP no valida nada (CR-2.1).

## 7. Consultar task

```http
GET https://api.kie.ai/api/v1/jobs/recordInfo?taskId=<TASK_ID>
Authorization: Bearer <KIE_API_KEY>
```

Formato exacto **pendiente de confirmar**. Hipótesis inicial:

```json
{
  "code": 200,
  "data": {
    "taskId": "task_xxx",
    "status": "success",
    "audio_url": "...",
    "video_url": "...",
    "output": { "url": "..." }
  }
}
```

El cliente debe normalizar `status` a:

```text
pending | running | success | failed
```

## Polling sugerido

```text
POLL_INTERVAL_SECONDS=10
TASK_TIMEOUT_SECONDS=1800
```

## Errores típicos

```text
401  -> KIE_API_KEY inválida
413  -> archivo demasiado grande
429  -> rate limit; reintentar con backoff
5xx  -> reintentar (3x backoff exponencial)
```

## Retención y borrado

Importante a conocer al diseñar la UX. La documentación oficial
(`docs.kie.ai §6 Data Retention Policy`) distingue dos categorías:

- **Archivos subidos via File Upload API** (imágenes que el usuario sube):
  **24 horas**. Aplicable a la pantalla `Imágenes`. Constante en código:
  `KIE_UPLOAD_RETENTION_HOURS = 24`.
- **Media generada por los modelos** (audios TTS, videos avatar, **imágenes
  generadas por Nano Banana 2**): **14 días**. Aplicable a la pantalla `Audios`
  (TTS), a las imágenes generadas (`GeneratedImage` en la pantalla `Imágenes`)
  y a los videos del flow principal. Constante: `KIE_GENERATED_RETENTION_DAYS = 14`.
- **Log records** (texto + metadata de tasks): retenidos **2 meses**.
- URLs efímeras en `tempfile.redpandaai.co` heredan la ventana del recurso
  que las generó.

> ⚠️ **Inconsistencia histórica conocida (pre-Nano Banana 2)**: las
> pantallas `Imágenes` (uploads) y `Audios` (TTS) compartían un alias
> `KIE_FILE_RETENTION_DAYS = 14`. Para uploads de imágenes el valor real
> es **24h**, no 14 días. El alias se eliminó al introducir la
> separación uploaded/generated; ahora cada caller usa la constante
> correcta. La pantalla `Imágenes` mezcla ambos tipos pero formatea cada
> fila con su TTL correspondiente.

Consecuencia: el botón **Eliminar** en las pantallas `Imágenes` y `Audios`
**solo borra el registro local** (`data/jobs.db`). El archivo en Kie sigue
accesible hasta que expira. Si hace falta un borrado urgente por privacidad,
hay que contactar al soporte de Kie por Discord/Telegram.
