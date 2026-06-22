# 0005. Galería persistente de imágenes con TTL derivado de Kie

Fecha: 2026-05-31 Estado: Aceptado

## Contexto

El SPEC original (`docs/SPEC.md` §4 y §9) describe `JobRunner` como un flujo
lineal por job:

```text
validate → upload_image | create_audio → wait_audio → create_avatar → ...
```

cada job hacía su propio upload de la imagen y descartaba la URL devuelta tras
consumirla. Esto fuerza re-uploads cuando el usuario quiere lanzar varios jobs
con la misma imagen (caso común: probar varios prompts sobre el mismo modelo), y
desperdicia tanto ancho de banda como cuota de Kie.

Además, Kie tiene una **política fija de retención de 14 días** para los
archivos subidos (`docs.kie.ai §6 Data Retention Policy`) y **no expone endpoint
de borrado**. Cualquier capa que reutilice una URL devuelta por `upload_file`
necesita saber cuándo expira para no fallar con un 404.

## Decisión

Introducimos una **galería persistente de imágenes** desacoplada del job:

1. **Modelo `UploadedImage`** en `domain/models.py` con:
   - `id`, `label` (humano), `local_path`, `kie_url`, `kie_file_path`,
     `file_size`, `mime_type`, `uploaded_at`.
   - Métodos `expires_at(retention_days)`, `is_expired(...)`, `time_left(...)`
     derivados puramente de `uploaded_at`.

2. **Constante de política** `KIE_FILE_RETENTION_DAYS = 14` en
   `domain/policies.py`, única fuente de verdad sobre el TTL. Si Kie cambia la
   política, solo se toca acá.

   > ⚠️ **Inconsistencia con la doc oficial detectada después** (ver ADR-0006
   > §"Aclaración sobre retención"): la doc Kie distingue **24h para uploads**
   > (file-upload-api) y **14d para generated media** (TTS, videos). Las
   > imágenes que cargamos via `upload_file` caen en la primera categoría → el
   > TTL real es **24h**, no 14 días. En código está separado como
   > `KIE_UPLOAD_RETENTION_HOURS = 24` y `KIE_GENERATED_RETENTION_DAYS = 14`,
   > pero `KIE_FILE_RETENTION_DAYS` sigue siendo un alias de 14 días para no
   > romper a `UploadedImage` mientras se planifica la migración. Cuando se haga
   > el switch en Fase 2.2d se debe convertir el TTL de imágenes a horas y
   > revisar el formato de "Expira" en la pantalla (días → horas).

3. **Persistencia** en una tabla nueva `uploaded_images` dentro de
   `data/jobs.db` (no se crea un segundo archivo SQLite — comparte WAL).
   Implementado en `infra/images_db.py` como `ImagesDB` separado de `JobsDB` (un
   repo por agregado, SRP).

4. **Casos de uso** en `app_layer/images_controller.py`:
   - `upload(local_path, label)` — sube a Kie y persiste.
   - `list_uploaded()`, `delete(id)`.
   - `get_for_use(id)` — devuelve la imagen si está fresca; lanza
     `ImageExpiredError` si superó el TTL o `ImageNotFoundError` si no existe.
     **Toda capa que vaya a reutilizar la URL en un job debe pasar por este
     método** para fallar pronto y con un error claro.
   - `cleanup_expired()` — quita registros locales cuyo TTL ya venció.
     Idempotente. Llamado desde el composition root al arrancar.

5. **UX**: pantalla `Imágenes` (atajo `I`) con CRUD + visor (local o URL en
   navegador como fallback) + copiar URL al clipboard. La columna "Path Kie"
   muestra `kie_file_path`, NO `kie_url`, para evitar que el `DataTable` de
   Textual auto-genere links clickeables sobre URLs truncadas con `…`.

## Impacto sobre `JobRunner` (Fase 2.3 — pendiente)

El contrato del job cambia respecto del SPEC original:

- `VideoJob` ya no debe disparar `upload_image` interno. En su lugar referencia
  una `UploadedImage.id` previamente persistida.
- Antes de encolar un job, la UI/CLI debe llamar a
  `ImagesController.get_for_use(image_id)`. Si lanza `ImageExpiredError`, el job
  no se encola y el usuario debe **cargar una nueva imagen** (no hay forma de
  "renovar" una URL en Kie sin re-uploadear el binario).
- El `JobRunner.upload_image` deja de existir como paso de la state machine; se
  reemplaza por una resolución `image_id → kie_url`.

Esto se reflejará en `docs/SPEC.md` cuando se cierre Fase 2.3.

## Consecuencias

### Positivas

- Reuso: una imagen subida una vez sirve para múltiples jobs.
- Cuota Kie ahorrada: menos uploads.
- Errores tipados: `ImageExpiredError` permite UX clara ("cargá una nueva") en
  lugar de tracebacks de Kie 4xx.
- Política centralizada: cambiar `KIE_FILE_RETENTION_DAYS` propaga a toda la app
  (controller + UI + cleanup).
- Cleanup automático al arrancar evita basura acumulada.

### Negativas

- Modelo más complejo: ahora hay dos agregados (`VideoJob` y `UploadedImage`) en
  lugar de uno.
- El usuario necesita un paso previo (cargar imagen) antes de crear su primer
  job. Mitigación: la futura pantalla `new_job` podrá ofrecer "cargar nueva
  imagen" como atajo dentro del flujo.

### Riesgos a vigilar

- Si Kie cambia la política de 14 días sin avisar, los TTL derivados quedan
  desfasados. Mitigación parcial: en algún punto vale exponer
  `KIE_FILE_RETENTION_DAYS` como `Settings.kie_file_retention_days` para
  override por usuario.
- `cleanup_expired()` corre solo al arrancar la app. Una sesión muy larga (>14
  días) puede dejar pasar imágenes expiradas. Mitigación: `get_for_use` valida
  igualmente en cada uso.
- El cleanup no se puede desactivar. Edge case: usuario quiere hacer backup
  manual de `data/jobs.db` y al abrir la app pierde filas expiradas. Mitigación
  futura: `Settings.cleanup_images_on_start: bool`.

## Alternativas consideradas

- **Embedder upload dentro de cada job** (estado del SPEC original): re-up por
  job, sin reuso. Rechazada por costo (cuota Kie) y porque el usuario pidió
  "almacenar esas URLs y previsualizar las fotos cargadas".
- **Cachear solo la URL en memoria por sesión**: pierde el reuso entre reinicios
  de la app y bloquea features como "rehacer un job antiguo con misma imagen".
- **Tabla `uploaded_images` en archivo SQLite separado**: agrega un segundo path
  de IO y rompe la convención `data/jobs.db` como único store local. Mantenemos
  un solo archivo, dos tablas.
- **JSON file** (similar a `data/keys.json`): rechazada porque imágenes pueden
  crecer a decenas de filas y SQLite con índice por `uploaded_at` rinde mejor;
  además `aiosqlite` ya está cableado.
- **Confiar 100% en validación al usar (sin cleanup automático)**: deja basura
  acumulada en DB para siempre. Cleanup es barato y mejora la UX de la galería
  (no aparecen filas muertas).
