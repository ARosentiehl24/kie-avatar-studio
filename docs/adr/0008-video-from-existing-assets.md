# 0008. Video desde assets reusables (skip upload + skip TTS)

Fecha: 2026-06-04
Estado: Aceptado

## Contexto

La pantalla `Nuevo video` estaba pendiente en `pending_message` desde
Fase 0. El SPEC original asumía que cada `VideoJob` se creaba "from
scratch": el usuario daba foto local + script + voz + prompt, y el
`JobRunner` siempre subía la imagen y creaba el task TTS.

Pero después del ADR-0007 (cola estructurada de audios) y del trabajo
de Imágenes (Fase 1.5), el usuario ya tiene **dos catálogos
reusables** en su instalación:

- `UploadedImage`s ya en Kie (subidas desde la pantalla Imágenes).
- `GeneratedAudio`s ya en Kie (generados desde la pantalla Audios).

Forzar al usuario a re-subir la imagen y re-generar el audio para
cada video sería:

1. **Caro**: TTS cuesta créditos. Subir la imagen consume slot de
   storage.
2. **Lento**: dos pasos secuenciales (upload + TTS) antes de poder
   empezar el avatar.
3. **Redundante**: la imagen ya está en Kie con su `kie_url`; el
   audio también con su `kie_url`. Ambos son válidos por el TTL
   estándar (14 días).

## Decisión

Habilitar el "**Modo B: video desde assets**" como primer modo de la
pantalla `Nuevo video`. El user-flow es:

```
1. Imágenes  → subo 1 foto del avatar (1 sola vez por proyecto)
2. Audios    → genero el TTS (1 vez por script único)
3. Videos    → 'Nuevo video' → elijo imagen + audio + prompt → ¡Generar!
4. Historial → veo el resultado en outputs/<id>/final.mp4
```

El "modo A" (from scratch) queda postergado: si el usuario quiere
generar audio + video en un solo flow, primero usa Audios y después
Videos. Es 1 click extra de UX a cambio de cero duplicación de
lógica en la UI.

### Cambios técnicos

**Domain (`models.py`, `policies.py`)**

- `VideoJob`: `script`, `image_path`, `voice` ahora son opcionales
  (`default=""`). En el modo reuse vienen como metadata informativa
  porque `audio_url`/`image_url` ya están poblados.
- `validate_job`: solo valida `script`/`voice` si `audio_url`
  está vacío; solo valida `image_path` si `image_url` está vacío.
  El prompt siempre se requiere.

**App layer**

- `JobRunner._produce_inputs` ahora delega en dos helpers:
  - `_upload_image_if_needed(job)`: si `image_url` ya está poblado,
    devuelve esa URL sin subir nada.
  - `_create_audio_if_needed(job)`: si `audio_url` ya está poblado,
    devuelve esa URL sin crear task TTS.
  - Retrocompat total: si ambos están `None`, hace upload + TTS desde
    cero como antes (modo A, sigue funcionando si en el futuro
    implementamos el form correspondiente).
- `VideosController` nuevo. API simétrica al `AudiosController`:
  - `enqueue_from_assets(image_id, audio_id, prompt)`: resuelve los
    assets desde sus stores, rechaza inexistentes o expirados,
    arma el `VideoJob` con URLs ya pobladas y encola.
  - `wait_for_job`, `subscribe`, `cancel`, `retry`, `delete_job`,
    `list_video_jobs`, `get_video_job`: shape idéntico al de audios.

**UI**

- `NewVideoFormScreen`: modal con Select imagen + Select audio +
  TextArea prompt + botón Preview de audio. Mismo patrón sticky
  footer del modal Generar audio (scrollbar solo en body).
- `VideosScreen`: tabla unificada de `VideoJob` con badges de estado,
  panel de contadores en vivo, acciones contextuales (Abrir mp4,
  Copiar URL, Cancelar, Reintentar, Quitar). Se suscribe al stream
  del `queue` de video en `on_mount`, desuscribe en `on_unmount`.
- Hotkey `n` (Nuevo) ahora abre `VideosScreen` en vez de mostrar
  el `pending_message`.

## Consecuencias

**Positivas**

- **Cero gasto duplicado**: si reusás imagen + audio, solo pagás los
  créditos del avatar (Kling AI).
- **Workflow rápido**: 1 click en `Nuevo video` + Select + Prompt
  + Generar (vs subir + esperar TTS + esperar avatar).
- **Idempotencia preservada**: el `final.mp4` queda en
  `outputs/<job_id>/final.mp4`. Reintentar el job genera un mp4
  nuevo en el mismo path (sobreescribe), no acumula basura.
- **DRY total**: el runner sigue siendo uno solo; solo cambia el
  punto de entrada del `VideoJob` (URLs pre-pobladas vs paths).
- **Asimetría sana de controllers**: `VideosController` orquesta
  desde fuera; el runner sigue ignorando de dónde vinieron las
  URLs. SOLID DIP intacto.

**Negativas**

- **No hay form "from scratch"**: si el usuario quiere "todo en un
  click", tiene que ir Audios → Videos. Aceptable porque casi
  siempre el audio se itera (regenerar TTS con otro tono) antes de
  comprometer el video.
- **`output_path` y `video_url`**: el `Quitar` del registro local
  no borra el `final.mp4` del disco ni la URL en Kie. Es
  intencional (el binario es del usuario), pero conviene que el
  usuario lo sepa. Mensaje en el hint de la pantalla.
- **`script` y `voice` informativos**: el `VideoJob` los copia desde
  el `GeneratedAudio` para que el historial/UI tenga contexto, pero
  el runner los ignora (porque `audio_url` está poblado). Si el
  usuario edita el `GeneratedAudio` después de crear el job, el
  contexto puede quedar desactualizado. Aceptable porque el audio
  ya está en Kie y no cambia.

## Alternativas consideradas

- **Form único con tabs "from scratch" vs "desde assets"**:
  rechazado. Complica el form, no sumamos beneficio inmediato.
  Cuando el "from scratch" haga falta, se agrega como segundo
  modal o tab.
- **`VideosController.enqueue_from_scratch`**: lo dejamos planeado
  para Fase 3 (junto con el form correspondiente). API sería:
  `enqueue_from_scratch(image_path, script, voice_id, prompt,
  voice_settings=None) -> VideoJob`.
- **No tocar el `JobRunner`**: hacer un `AvatarOnlyRunner` separado
  que solo orqueste el avatar. Rechazado por DRY: la lógica de
  polling + persist + transición ya está bien en `JobRunner`, no
  vale duplicarla.

## Cumplimiento

- `docs/ROADMAP.md` Fase 2 ahora marca "Pantalla `new_job`"
  cerrada con Modo B; la versión "from scratch" queda en Fase 3.
- 21 tests nuevos: `test_videos_controller.py` (10),
  `test_job_runner_skip.py` (3), `test_videos_screen.py` (8).
- 381 tests verdes en total (era 360 antes del refactor), ruff +
  mypy strict + import-linter 4/4 KEPT.
- Smoke test manual: arrancar → `n` → Nuevo video → Select pobla
  con imagen y audio reales → Modal funciona OK.
