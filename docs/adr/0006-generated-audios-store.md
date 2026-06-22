# 0006. Galería persistente de audios TTS con TTL y catálogo built-in

Fecha: 2026-05-31 Estado: Aceptado

## Contexto

El SPEC original (`docs/SPEC.md` §4) describe el TTS como un paso interno de
`JobRunner.create_audio`: por cada job se sintetiza un audio nuevo, se consume
su URL y se descarta. Replica el mismo problema que ya resolvimos con imágenes
en ADR-0005 (ver §"Decisión" allí):

- Re-síntesis innecesaria cuando el usuario quiere reusar el mismo audio en
  varios jobs (probar varios prompts sobre el mismo guion/voz).
- Sin galería, no hay forma de auditar/escuchar audios pasados.
- Las URLs `tempfile.redpandaai.co` tienen TTL y nadie lo enforce-ea pronto:
  cualquier reuso silencioso a los 15 días explota con 404.

Además, durante la investigación previa a Fase 2.2c salieron 3 hallazgos sobre
el API de voces de Kie que condicionan el diseño:

1. **No hay endpoint para listar voces dinámicamente**. Verificado contra el
   sitemap completo (`docs.kie.ai/sitemap.xml`) + `llms.txt` + Common API. Las
   67 voces curadas viven embebidas en la descripción del campo `voice` del
   OpenAPI YAML del endpoint TTS.
2. **Kie no expone Voice Design por prompt**. El único modelo que genera un
   `voiceId` desde input es `suno-api/voice/generate`, pero (a) parte de un
   sample de audio del usuario, no de un prompt, y (b) el voiceId resultante
   solo sirve para los modelos musicales de Suno.
3. **El endpoint TTS acepta voice_ids fuera del enum** (cuentas ElevenLabs Pro,
   voces clonadas en la UI de ElevenLabs). El catálogo "built-in" es
   informativo, no una restricción dura del API.

## Decisión

Replicamos el patrón de ADR-0005 para audios + empotrar el catálogo:

1. **Modelo `GeneratedAudio`** en `domain/models.py` con:
   - `id`, `label`, `script` (texto completo, para auditoría), `voice_id`,
     `voice_settings: VoiceSettings | None`, `kie_url`, `kie_file_path`,
     `file_size`, `mime_type`, `duration_seconds`, `generated_at`.
   - Helpers `expires_at(retention_days)`, `is_expired(...)`, `time_left(...)`
     con la **misma firma** que `UploadedImage` (CR-3.7).
   - `file_size`, `mime_type` y `duration_seconds` son `Optional` — Kie no
     siempre los devuelve en `recordInfo` y la UI los muestra como "—".

2. **Modelo `VoiceSettings`** en `domain/models.py` con los 5 campos opcionales
   del input TTS: `stability`, `similarity_boost`, `style` (0.0-1.0), `speed`
   (0.7-1.2), `language_code` (ISO 639-1, solo turbo/flash v2.5). Rangos
   enforceados por `Field` de Pydantic; la validación semántica adicional
   (formato ISO) vive en `policies.validate_voice_settings`.

3. **Catálogo built-in** en `domain/kie_voice_catalog.py`:
   - `KieVoice` modelo con `voice_id`, `label`, `description` (puede estar
     vacío) y propiedades derivadas `preview_url`, `display_name`.
   - `BUILTIN_VOICES: tuple[KieVoice, ...]` con las 67 voces parseadas del
     OpenAPI spec, **tuple** para inmutabilidad.
   - Helpers `get_builtin_voice(voice_id)` y `is_builtin_voice(voice_id)` que
     consultan un dict precomputado O(1).
   - Mantenimiento: cuando Kie publique cambios al spec hay que re-sincronizar a
     mano. No worth automation: el catálogo cambia poco y bajar/parsear el YAML
     en runtime suma complejidad para 0 beneficio.

4. **Política de retención** dentro de `domain/policies.py`:
   - `KIE_GENERATED_RETENTION_DAYS = 14` — para audios y videos.
   - `KIE_UPLOAD_RETENTION_HOURS = 24` — agregada para futuro uso en imágenes
     (uploads), todavía no aplicada para no romper ADR-0005.
   - `KIE_FILE_RETENTION_DAYS` queda como alias backwards-compat (=14) usado por
     `UploadedImage`. Migración pendiente.

5. **Persistencia** en una tabla nueva `generated_audios` dentro del mismo
   `data/jobs.db` (mismo principio que `uploaded_images` en ADR-0005 — un solo
   archivo SQLite WAL, una tabla por agregado). Implementado en
   `infra/audios_db.py` como `AudiosDB`. El `voice_settings` se persiste como
   JSON nullable (`TEXT`) para no tener que agregar columnas cuando ElevenLabs
   sume parámetros nuevos al spec.

6. **Casos de uso** en `app_layer/audios_controller.py`:
   - `generate(label, script, voice_id, voice_settings)` — valida con policies,
     crea task vía `KieGateway.create_tts_task`, hace polling con
     `poll_task_for_url` (helper compartido — ver §"Polling helper"), persiste
     el `GeneratedAudio`.
   - `list_generated`, `delete`, `get_for_use` (rechaza expirados con
     `AudioExpiredError`), `cleanup_expired` (idempotente, corre al arrancar).

7. **Polling helper compartido**: extraído `poll_task_for_url` a
   `app_layer/polling.py` para evitar duplicación entre `JobRunner` y
   `AudiosController` (CR-3.7). Ambos pasan el mismo `KieGateway` + los
   timeouts/interval de sus respectivos `Settings`. `JobRunner._poll_for_url`
   queda como wrapper fino que delega.

8. **UX**:
   - Pantalla `Audios` (atajo `A`) con la misma estética que `Imágenes`:
     `DataTable` + botones **Generar / 🔊 Escuchar / Copiar URL / Quitar**.
   - Modal `GenerateAudioFormScreen` con `Input` label + `TextArea` script
     (contador 0/5000 que se torna rojo al pasarse) + `Select` voice (poblado
     con `BUILTIN_VOICES.display_name`) + botón **🔊 Preview voz** que abre el
     MP3 estático en el reproductor del SO + `Collapsible` "Avanzado" con los 5
     inputs de `voice_settings`.
   - Reproducción y preview delegados a `system_opener.open_url` (mismo helper
     que usa Imágenes para "Ver"). Multi-plataforma, zero dependencias extra. La
     URL se copia siempre al clipboard antes de invocar al launcher como
     fallback si éste falla.
   - La columna "Path Kie" muestra `kie_file_path` (no `kie_url`) para evitar el
     bug del `DataTable` que auto-genera links clickeables sobre URLs truncadas
     con `…` (mismo fix que en ADR-0005).

## Decisiones explícitamente descartadas

- **Pantalla `Presets` para CRUD de voice_ids custom** — pospuesto. Solo los 67
  built-in se ofrecen en el `Select`. Razón: 67 ya es un pool suficiente para la
  mayoría de casos de uso y agregar otra pantalla (con su store, controller,
  modal, tests) hubiera duplicado el tamaño del PR. Quien tiene voice_ids custom
  (cuentas ElevenLabs Pro) puede usarlos en el futuro cuando agreguemos la
  pantalla; el endpoint Kie ya los acepta, lo que falta es la UI.
- **Descargar el MP3 localmente** — descartado por el usuario. Solo guardamos
  `kie_url`. Resultado: storage mínimo, escuchar requiere conexión durante los
  14 días que dura el archivo en Kie.
- **Reproductor en background con `mpv`/`ffplay`** — pospuesto a 2.2d.
  Requeriría detectar binarios disponibles, fallback a webbrowser, y documentar
  deps del SO. `webbrowser.open` resuelve el caso común sin dependencias.
- **Voice Design desde prompt** — no es decisión nuestra: Kie no lo expone. Si
  en una fase futura lo agrega como modelo, se registra en un ADR nuevo.

## Aclaración sobre retención

La inconsistencia con ADR-0005 se descubrió durante esta fase:
`docs.kie.ai §6 Data Retention Policy` distingue **24h para uploads** y **14d
para generated media**. Para audios la regla es clara (14d) y queda implementada
con `KIE_GENERATED_RETENTION_DAYS`. Para imágenes (uploads via
`file-stream-upload`) el TTL real es 24h, pero seguir usando 14d "no rompe" — el
peor caso es que la app deje pasar un registro expirado al `get_for_use`, que
ahí va a recibir 404 de Kie. Migración formal a `KIE_UPLOAD_RETENTION_HOURS`
queda como tarea de cleanup separada.

## Consecuencias

### Positivas

- Reuso: un audio generado una vez sirve para múltiples jobs (mismo guion +
  diferentes prompts).
- Cuota Kie ahorrada: menos invocaciones TTS.
- Errores tipados: `AudioExpiredError` permite UX clara ("regeneralo"), análoga
  a `ImageExpiredError`.
- Catálogo built-in: el usuario no necesita ir a ElevenLabs a buscar voice_ids,
  salen de un Select con preview.
- Polling helper compartido reduce ~20 líneas de duplicación entre `JobRunner` y
  `AudiosController` y centraliza el cambio si el shape de `recordInfo` se
  mueve.

### Negativas

- Catálogo manual: si Kie agrega/saca voces, hay que actualizar
  `kie_voice_catalog.py` y los tests (count = 67 hardcoded).
- Tabla más compleja: 11 columnas vs 8 de `uploaded_images`. JSON column para
  settings agrega un mapper en los dos sentidos.
- Voice cloning desde audio sample queda fuera de scope (requeriría endpoints
  Suno + cambio de modelo, no aplicable a TTS de avatar).

### Riesgos a vigilar

- Si Kie expone un endpoint de listado de voces en el futuro, el catálogo
  estático queda desactualizado. Mitigación: usar el endpoint cuando aparezca
  (Fase futura con su propio ADR).
- `duration_seconds` queda en `None` permanente hasta confirmar el shape exacto
  del `recordInfo` de TTS (TODO en el código).
- Modelo `multilingual-v2` rechaza `language_code`; el spec dice que solo
  turbo/flash v2.5 lo aceptan. El modal expone el campo igual porque queremos
  preparar el switch al turbo en una futura iteración; por ahora la
  responsabilidad de no setearlo cae en el usuario.

## Alternativas consideradas

- **Sin galería, audio inline en cada job** (estado original del SPEC):
  re-síntesis por job, sin reuso. Rechazada por costo (cuota Kie) y porque el
  patrón de imágenes ya marcó el precedente de galería.
- **Catálogo de voces servido desde JSON descargado en runtime**: parse
  on-the-fly del OpenAPI spec con `Sincronizar catálogo` en la TUI. Rechazada:
  agrega red+parsing+caché para un dataset que cambia poco (Kie no agrega voces
  frecuentemente). El esfuerzo no se justifica hasta que cambie 2-3 veces en el
  año.
- **Voice_settings como tabla normalizada** en lugar de JSON column: rechazada
  porque el schema de ElevenLabs sigue evolucionando (ya agregaron `speed`,
  `language_code`) y JSON aguanta cambios sin migrar la tabla.
- **Polling duplicado en cada controller**: rechazada por CR-3.7. El helper
  compartido es estricto-additive (no rompe `JobRunner`) y la migración es
  trivial.
- **Sin `preview_url` derivada**: forzaría a la UI a construir la URL por su
  cuenta. Centralizar en el modelo evita números mágicos repetidos
  (`https://static.aiquickdraw.com/...`) en 2+ lugares.
