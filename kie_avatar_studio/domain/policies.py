"""Validaciones del dominio y normalizadores de respuestas de Kie.

Concentra todos los límites duros del proveedor en constantes nombradas,
para que ni el cliente HTTP ni el runner contengan números mágicos.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from .errors import (
    AudioValidationError,
    ImageGenerationValidationError,
    ImageValidationError,
    JobValidationError,
    KeyValidationError,
    UrlValidationError,
    VoiceSettingsValidationError,
    WorkflowStepValidationError,
    WorkflowValidationError,
)
from .kie_voice_catalog import is_builtin_voice
from .models import (
    ImageAssetRef,
    ImageGenerationSettings,
    ModelCreation,
    ModelCreationMethod,
    StepType,
    VideoJob,
    VoiceSettings,
    WorkflowJob,
    WorkflowProgressKey,
    WorkflowStep,
)

MAX_SCRIPT_CHARS: Final[int] = 5000
MAX_PROMPT_CHARS: Final[int] = 5000
_BYTES_PER_MB: Final[int] = 1024 * 1024
MAX_IMAGE_BYTES: Final[int] = 10 * _BYTES_PER_MB
MAX_AUDIO_BYTES: Final[int] = 100 * _BYTES_PER_MB
MAX_AUDIO_SECONDS: Final[int] = 5 * 60

# Restricciones del endpoint Nano Banana 2 (`docs.kie.ai/market/google/nanobanana2`).
# El prompt admite hasta 20.000 chars (mucho más generoso que el de avatar/TTS)
# y la API acepta hasta 14 refs como `image_input` (cada una debe ser una URL
# pública, jpg/png/webp ≤ 30 MB). Validamos cantidad y forma de las URLs;
# el tamaño y mimetype reales los enforcea Kie al consumir la URL.
MAX_IMAGE_PROMPT_CHARS: Final[int] = 20000
MAX_IMAGE_REFS: Final[int] = 14

# Enums del input del endpoint. Mantenemos el orden del spec para que la UI
# los muestre tal como aparecen en la doc oficial. `auto` es el default del
# spec y representa "Nano Banana decide según las refs / el prompt".
ASPECT_RATIOS: Final[tuple[str, ...]] = (
    "auto",
    "1:1",
    "2:3",
    "3:2",
    "3:4",
    "4:3",
    "4:5",
    "5:4",
    "9:16",
    "16:9",
    "21:9",
    "1:4",
    "1:8",
    "4:1",
    "8:1",
)
RESOLUTIONS: Final[tuple[str, ...]] = ("1K", "2K", "4K")
OUTPUT_FORMATS: Final[tuple[str, ...]] = ("jpg", "png")

# Política de retención de Kie. La documentación oficial (docs.kie.ai §6 Data
# Retention Policy) distingue dos categorías:
#
# - Archivos subidos por el usuario via File Upload API: **24 horas**.
# - Media generada por los modelos (TTS, image gen, video gen): **14 días**.
#
# Antes existía un alias `KIE_FILE_RETENTION_DAYS` apuntando a la constante
# de generated media (14 días) por compatibilidad — pero eso era incorrecto
# para imágenes y causaba que el avatar fallara con "Image fetch failed"
# cuando se intentaba usar una imagen ya expirada en Kie (>24h). El alias
# se eliminó; ahora cada caller usa la constante correcta según el tipo
# de recurso.
KIE_GENERATED_RETENTION_DAYS: Final[int] = 14
KIE_UPLOAD_RETENTION_HOURS: Final[int] = 24

IMAGE_EXTENSIONS: Final[frozenset[str]] = frozenset({".png", ".jpg", ".jpeg"})

# Restricciones para `KieKey`. La key real de Kie es un token largo opaco;
# 8 caracteres es el mínimo defensivo (cubre el caso de pegar algo
# notoriamente vacío o un placeholder).
MIN_KEY_LENGTH: Final[int] = 8
MAX_KEY_LENGTH: Final[int] = 512
MIN_LABEL_LENGTH: Final[int] = 1
MAX_LABEL_LENGTH: Final[int] = 64
_LABEL_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[\w\s\-.]+$", re.UNICODE)

NormalizedStatus = Literal["pending", "running", "success", "failed"]

_STATUS_SYNONYMS: Final[dict[str, NormalizedStatus]] = {
    "": "pending",
    "pending": "pending",
    "queued": "pending",
    "waiting": "pending",
    "running": "running",
    "in_progress": "running",
    "processing": "running",
    "success": "success",
    "succeeded": "success",
    "completed": "success",
    "done": "success",
    "failed": "failed",
    "fail": "failed",
    "error": "failed",
    "cancelled": "failed",
}

# Kie reporta el estado en distintos nombres según el endpoint y la version
# del API. Probamos en orden de prevalencia observada en respuestas reales:
#   - `state` (visto en `recordInfo` de TTS y avatar).
#   - `status` (mencionado en docs.kie.ai cheatsheet original).
#   - `taskStatus` / `task_status` (variantes vistas en algunos modelos).
_STATUS_FIELD_KEYS: Final[tuple[str, ...]] = (
    "state",
    "status",
    "taskStatus",
    "task_status",
)

# Claves dentro de `data` que pueden contener directamente la URL del
# resultado (compatibilidad con shapes viejos / otros modelos).
_RESULT_URL_KEYS: Final[tuple[str, ...]] = (
    "audio_url",
    "video_url",
    "result_url",
    "resultUrl",
)


def validate_job(job: VideoJob) -> None:
    """Aplica las restricciones de Kie sobre un `VideoJob`.

    Lanza `JobValidationError` con un mensaje en español describiendo la regla
    violada. Es responsabilidad del caller decidir si convierte el error en
    `job.status = FAILED` o lo presenta al usuario.

    En el "modo asset reuse" (cuando el job viene con `image_url` y/o
    `audio_url` ya poblados desde Imágenes/Audios), las validaciones del
    asset original ya pasaron al subirlo/generarlo: acá solo validamos
    los campos que SÍ vamos a usar en este job. El prompt siempre se
    valida (no proviene de otro asset reusable hoy).
    """
    if not job.prompt:
        raise JobValidationError("prompt vacío")
    if len(job.prompt) > MAX_PROMPT_CHARS:
        raise JobValidationError(f"prompt supera {MAX_PROMPT_CHARS} caracteres")

    # Script + voz solo se validan si vamos a generar audio acá. Cuando
    # `audio_url` ya está poblado (reuso de GeneratedAudio), el script y
    # la voz son metadata informativa que ya pasó por su propia validación
    # al crear el audio TTS.
    if not job.audio_url:
        if not job.script:
            raise JobValidationError("script vacío")
        if len(job.script) > MAX_SCRIPT_CHARS:
            raise JobValidationError(f"script supera {MAX_SCRIPT_CHARS} caracteres")

    # Imagen local solo se valida si la vamos a subir acá. Cuando
    # `image_url` ya está poblado (reuso de UploadedImage), el archivo
    # original puede no existir más localmente — Kie ya tiene la copia.
    if not job.image_url:
        # Reutiliza la misma validación que aplica `ImagesController` al subir,
        # para que un job nunca refiera una imagen rechazable por Kie (CR-3.7).
        try:
            validate_image_path(Path(job.image_path))
        except ImageValidationError as exc:
            raise JobValidationError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class ImageMetadata:
    """Metadata sintáctica derivada del filesystem (no abre la imagen)."""

    path: Path
    size_bytes: int
    extension: str


def validate_image_path(path: Path) -> ImageMetadata:
    """Aplica las restricciones de Kie sobre una imagen local.

    Devuelve metadata útil para evitar volver a hacer `stat`/`suffix`.
    Lanza `ImageValidationError` con mensaje en español.
    """
    if not path.is_file():
        raise ImageValidationError(f"imagen no encontrada: {path}")
    extension = path.suffix.lower()
    if extension not in IMAGE_EXTENSIONS:
        raise ImageValidationError(f"formato de imagen no soportado: {extension}")
    size = path.stat().st_size
    if size > MAX_IMAGE_BYTES:
        raise ImageValidationError(f"imagen supera {MAX_IMAGE_BYTES // _BYTES_PER_MB} MB")
    if size == 0:
        raise ImageValidationError(f"imagen vacía: {path}")
    return ImageMetadata(path=path, size_bytes=size, extension=extension)


def validate_kie_key(value: str) -> None:
    """Valida que el string sea aceptable como secreto de Kie.

    No verifica contra el servidor — eso lo hace `KeysController.test_key`.
    Solo chequeos sintácticos para evitar guardar basura obvia.
    """
    if not value or value != value.strip():
        raise KeyValidationError("la API key no puede estar vacía ni tener espacios alrededor")
    if len(value) < MIN_KEY_LENGTH:
        raise KeyValidationError(f"la API key debe tener al menos {MIN_KEY_LENGTH} caracteres")
    if len(value) > MAX_KEY_LENGTH:
        raise KeyValidationError(f"la API key supera {MAX_KEY_LENGTH} caracteres")
    if any(ch.isspace() for ch in value):
        raise KeyValidationError("la API key no puede contener espacios internos")


def validate_key_label(value: str) -> None:
    """Valida el label legible asignado a una `KieKey`."""
    stripped = value.strip()
    if len(stripped) < MIN_LABEL_LENGTH:
        raise KeyValidationError("el label no puede estar vacío")
    if len(stripped) > MAX_LABEL_LENGTH:
        raise KeyValidationError(f"el label supera {MAX_LABEL_LENGTH} caracteres")
    if not _LABEL_PATTERN.match(stripped):
        raise KeyValidationError("el label solo admite letras, números, espacios, '-', '.' y '_'")


def normalize_task_status(raw: str | None) -> NormalizedStatus:
    """Mapea cualquier valor de status/state a uno de los 4 estados canónicos."""
    key = (raw or "").strip().lower()
    return _STATUS_SYNONYMS.get(key, "running")


def extract_task_status(detail: dict[str, Any]) -> NormalizedStatus:
    """Lee el estado del task desde el payload de `recordInfo`.

    Kie usa `state` (no `status`) en `recordInfo` de la mayoría de los
    modelos en el API actual; mantenemos `status` como fallback por
    compatibilidad histórica. Devuelve siempre uno de los 4 estados
    canónicos; si no encuentra ninguno reconocido, asume `running` para
    seguir polling (más seguro que asumir failed).
    """
    data = detail.get("data") if isinstance(detail, dict) else None
    if not isinstance(data, dict):
        return "running"
    for key in _STATUS_FIELD_KEYS:
        raw = data.get(key)
        if isinstance(raw, str) and raw:
            return normalize_task_status(raw)
    return "running"


def extract_failure_message(detail: dict[str, Any]) -> str | None:
    """Si Kie reportó un error, devuelve el mensaje legible (`failMsg`/`msg`).

    Pensada para enriquecer el mensaje de `KieError` cuando un task termina
    como `failed`. Devuelve `None` si no hay info disponible.
    """
    data = detail.get("data") if isinstance(detail, dict) else None
    if not isinstance(data, dict):
        return None
    for key in ("failMsg", "fail_msg", "errorMsg", "error_msg", "msg"):
        msg = data.get(key)
        if isinstance(msg, str) and msg:
            return msg
    return None


def extract_result_url(detail: dict[str, Any]) -> str | None:
    """Busca la URL del resultado en el payload de `recordInfo`.

    Soporta tres shapes observados en Kie real:
    1. `data.resultJson` (string JSON serializado) → `resultUrls[0]`. Es el
       formato actual usado por TTS y avatar (visto en respuestas 2026).
    2. `data.audio_url` / `data.video_url` / `data.result_url` directos
       (mencionado en docs.kie.ai cheatsheet original).
    3. `data.output.url` (fallback para algunos modelos legacy).

    Devuelve `None` si no encuentra ninguna — el caller decide si esperar
    o fallar.
    """
    data = detail.get("data") if isinstance(detail, dict) else None
    if not isinstance(data, dict):
        return None
    url = _extract_from_result_json(data.get("resultJson"))
    if url:
        return url
    for key in _RESULT_URL_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    output = data.get("output")
    if isinstance(output, dict):
        value = output.get("url")
        if isinstance(value, str) and value:
            return value
    return None


def _extract_from_result_json(raw: object) -> str | None:
    """Decodifica `resultJson` (string JSON) y extrae la primera URL.

    Acepta tanto `{"resultUrls": [...]}` (formato observado en TTS) como
    objetos con una sola URL en otras claves comunes. Silencia errores
    de parsing para no propagar `JSONDecodeError` desde el dominio.
    """
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    urls = parsed.get("resultUrls") or parsed.get("result_urls")
    if isinstance(urls, list):
        for candidate in urls:
            if isinstance(candidate, str) and candidate:
                return candidate
    # Algunos endpoints serializan `{"resultUrl": "..."}` (singular) dentro
    # del JSON anidado. Lo cubrimos también.
    for key in ("resultUrl", "url", "audioUrl", "videoUrl"):
        value = parsed.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def is_path_inside(path: Path, root: Path) -> bool:
    """Guardia anti path-traversal: `path` debe quedar bajo `root` tras resolver."""
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


_ALLOWED_URL_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})


def validate_http_url(url: str) -> None:
    """Valida que `url` sea una URL http(s) bien formada.

    Pensada para usarse antes de pasar la URL a un launcher externo
    (`xdg-open`, `os.startfile`). Evita que un esquema raro (`file://`,
    `javascript:`, vacío) termine abriendo algo no esperado.

    Lanza `UrlValidationError` con mensaje en español si la URL no es aceptable.
    """
    if not url or url != url.strip():
        raise UrlValidationError("la URL no puede estar vacía ni tener espacios alrededor")
    if any(ch.isspace() for ch in url):
        raise UrlValidationError("la URL no puede contener espacios internos")
    scheme, separator, _rest = url.partition("://")
    if not separator or scheme.lower() not in _ALLOWED_URL_SCHEMES:
        raise UrlValidationError(
            f"la URL debe empezar con http:// o https:// (recibí: {url[:32]!r})"
        )


# Mínimo defensivo del voice_id: ElevenLabs usa IDs de 20 chars (p. ej.
# `N2lVS1w4EtoT3dr4eOWO`). Algunos preset names alternativos como "Rachel" o
# "Adam" tienen 4-6 chars y también son válidos. 3 es un umbral conservador
# que rechaza basura obvia sin bloquear casos legítimos.
MIN_VOICE_ID_LENGTH: Final[int] = 3
MAX_VOICE_ID_LENGTH: Final[int] = 64
# Acepta tanto ISO 639-1 ("es", "en") como BCP 47 ("es-419", "pt-BR").
# Validamos solo formato; Kie/ElevenLabs valida el código real.
_LANGUAGE_CODE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z]{2}(-[A-Za-z0-9]{2,8})?$")


def validate_tts_script(text: str) -> None:
    """Valida el texto a sintetizar como audio TTS.

    Mismo límite de chars que el script de video (5000) — es la cota que
    impone el endpoint `elevenlabs/text-to-speech-multilingual-v2`.
    """
    if not text or text != text.strip():
        raise AudioValidationError("el script TTS no puede estar vacío ni tener espacios alrededor")
    if len(text) > MAX_SCRIPT_CHARS:
        raise AudioValidationError(f"el script TTS supera {MAX_SCRIPT_CHARS} caracteres")


def validate_voice_id(voice_id: str, *, allow_custom: bool = True) -> None:
    """Valida un `voice_id` de ElevenLabs vía Kie.

    Por defecto admite voice_ids fuera del catálogo built-in: el endpoint de
    Kie acepta IDs de voces clonadas en cuentas ElevenLabs Pro. Si el caller
    necesita restringir al catálogo curado (p. ej. UI con Select fijo), pasa
    `allow_custom=False`.
    """
    if not voice_id or voice_id != voice_id.strip():
        raise AudioValidationError("el voice_id no puede estar vacío ni tener espacios alrededor")
    if any(ch.isspace() for ch in voice_id):
        raise AudioValidationError("el voice_id no puede contener espacios internos")
    if len(voice_id) < MIN_VOICE_ID_LENGTH:
        raise AudioValidationError(
            f"el voice_id debe tener al menos {MIN_VOICE_ID_LENGTH} caracteres"
        )
    if len(voice_id) > MAX_VOICE_ID_LENGTH:
        raise AudioValidationError(f"el voice_id supera {MAX_VOICE_ID_LENGTH} caracteres")
    if not allow_custom and not is_builtin_voice(voice_id):
        raise AudioValidationError(
            f"voice_id {voice_id!r} no pertenece al catálogo built-in de Kie"
        )


def validate_voice_settings(settings: VoiceSettings) -> None:
    """Valida `VoiceSettings` para emitir errores tipados en español.

    Pydantic ya enforce los rangos vía `Field`, pero esta función agrega
    chequeos semánticos extra (formato de `language_code`) y unifica el tipo
    de excepción que ven los controllers (`VoiceSettingsValidationError`).
    """
    if settings.language_code is not None:
        code = settings.language_code.strip().lower()
        if code and not _LANGUAGE_CODE_PATTERN.match(code):
            raise VoiceSettingsValidationError(
                "language_code debe ser un código ISO 639-1 ('es', 'en') o BCP 47 "
                "('es-419', 'pt-BR')"
            )


IMAGE_LABEL_MAX_LENGTH: Final[int] = 64


def validate_image_label(label: str) -> str:
    """Valida y limpia el label de una imagen generada.

    Lanza `ImageGenerationValidationError` si el label es vacío o supera el límite.
    """
    clean = label.strip()
    if not clean:
        raise ImageGenerationValidationError("el label de la imagen no puede estar vacío")
    if len(clean) > IMAGE_LABEL_MAX_LENGTH:
        raise ImageGenerationValidationError(
            f"el label de la imagen supera {IMAGE_LABEL_MAX_LENGTH} caracteres"
        )
    return clean


def validate_image_prompt(prompt: str) -> None:
    """Valida el prompt de generación de imagen para Nano Banana 2.

    Límite oficial: 20.000 chars (`docs.kie.ai/market/google/nanobanana2`).
    """
    if not prompt or prompt != prompt.strip():
        raise ImageGenerationValidationError(
            "el prompt no puede estar vacío ni tener espacios alrededor"
        )
    if len(prompt) > MAX_IMAGE_PROMPT_CHARS:
        raise ImageGenerationValidationError(
            f"el prompt supera {MAX_IMAGE_PROMPT_CHARS} caracteres"
        )


def validate_image_settings(settings: ImageGenerationSettings) -> None:
    """Valida que los enums del input estén dentro del catálogo del modelo.

    Pydantic no enforce los enums (los dejamos como strings para que el
    modelo sobreviva si Kie suma ratios/resoluciones nuevas sin rebuild
    de la app); la validación dura vive acá para que el caller emita
    el error tipado en español antes de pegarle a Kie.
    """
    if settings.aspect_ratio not in ASPECT_RATIOS:
        raise ImageGenerationValidationError(
            f"aspect_ratio inválido: {settings.aspect_ratio!r} (válidos: {', '.join(ASPECT_RATIOS)})"
        )
    if settings.resolution not in RESOLUTIONS:
        raise ImageGenerationValidationError(
            f"resolution inválido: {settings.resolution!r} (válidos: {', '.join(RESOLUTIONS)})"
        )
    if settings.output_format not in OUTPUT_FORMATS:
        raise ImageGenerationValidationError(
            f"output_format inválido: {settings.output_format!r} "
            f"(válidos: {', '.join(OUTPUT_FORMATS)})"
        )


def validate_image_refs(refs: list[ImageAssetRef]) -> None:
    """Valida las refs (`image_input`) de un job de generación.

    Reglas:
    - Hasta `MAX_IMAGE_REFS` (14 — límite duro del endpoint).
    - URLs http(s) bien formadas (reutiliza `validate_http_url`).
    - Sin duplicados por `kie_url` (Kie acepta duplicados pero no aportan
      nada y consumen un slot del máximo de 14).

    NO valida expiración: eso depende del momento de ejecución y lo hace
    el runner contra el store correspondiente justo antes de
    `create_nano_banana_task`. Una ref válida acá puede estar expirada
    al ejecutar el job si pasó mucho tiempo en la cola.
    """
    if len(refs) > MAX_IMAGE_REFS:
        raise ImageGenerationValidationError(
            f"máximo {MAX_IMAGE_REFS} refs permitidas (recibí {len(refs)})"
        )
    seen: set[str] = set()
    for ref in refs:
        try:
            validate_http_url(ref.kie_url)
        except UrlValidationError as exc:
            raise ImageGenerationValidationError(
                f"ref '{ref.label}' tiene URL inválida: {exc}"
            ) from exc
        if ref.kie_url in seen:
            raise ImageGenerationValidationError(f"ref '{ref.label}' está duplicada en la lista")
        seen.add(ref.kie_url)


# --- Workflow automation validators ---------------------------------------

# Restricciones del endpoint Kling 3.0 (`kling-3.0/video`, ver `docs.kie.ai/market/kling/kling-3-0`):
# - prompt máximo 2500 chars.
# - duración aceptada: entero 3-15 segundos (se serializa como string al body).
# Ver `docs/API_KIE.md §6`.
MAX_I2V_PROMPT_CHARS: Final[int] = 2500
I2V_DURATIONS: Final[tuple[int, ...]] = (3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15)

# Default global cuando ningún nivel (override del modal, step.duration_seconds
# del JSON) provee una duración explícita para un b-roll. La fuente canónica
# vive acá; `Settings.default_i2v_duration_seconds` la importa para que .env
# pueda override-arla sin desincronizarse con la UI (preview del summary).
DEFAULT_I2V_DURATION_SECONDS: Final[int] = 5

# Modos de generación de Kling 3.0 (resolución y costo dependen del modo).
# - `std`  -> 720p (16:9 = 1280x720). Más barato.
# - `pro`  -> 1080p (16:9 = 1920x1080). Default razonable.
# - `4K`   -> 2160p (16:9 = 3840x2160). Más caro y lento.
I2V_MODES: Final[tuple[str, ...]] = ("std", "pro", "4K")
DEFAULT_I2V_MODE: Final[str] = "pro"

# Aspect ratios de Kling 3.0. Cuando se pasa una imagen ref, Kling auto-adapta
# al ratio de la imagen y este campo se vuelve opcional.
I2V_ASPECT_RATIOS: Final[tuple[str, ...]] = ("16:9", "9:16", "1:1")
DEFAULT_I2V_ASPECT_RATIO: Final[str] = "16:9"


def resolve_effective_i2v_duration(
    override: int | None, step_value: int | None, default: int
) -> int:
    """Resuelve la duración del b-roll a usar en runtime.

    Precedencia (de más fuerte a más débil):

    1. `override`  — `WorkflowPreSettings.i2v_duration_seconds` del modal
       Configurar. Si está seteado FORZA a todos los b-roll del workflow
       (sobreescribe cualquier valor por step). La justificación es que
       el modal es la última palabra antes de encolar.
    2. `step_value` — `WorkflowStep.duration_seconds` del JSON. Decisión
       por escena del autor del workflow.
    3. `default`   — fallback global (`Settings.default_i2v_duration_seconds`,
       configurable vía `.env`).

    Esta es la fuente ÚNICA de la regla de precedencia. Tanto el runtime
    (`WorkflowExecutionContext.resolve_i2v_duration`) como la UI
    (`WorkflowSummaryScreen._render_steps_block`) la consumen para que no
    haya drift entre lo que se PREVIEWS y lo que se EJECUTA.
    """
    if override is not None:
        return override
    if step_value is not None:
        return step_value
    return default


def parse_optional_int_field(raw: object) -> int | None:
    """Convierte `raw` (proveniente de JSON crudo) a `int` o `None`.

    Acepta `int`, `str` numérica y `float` (con coerción). Rechaza tipos
    no convertibles devolviendo `None` para que el caller no tenga que
    distinguir entre "campo ausente del JSON" y "campo presente como
    string vacío o no numérico". El validador de dominio decide si
    `None` es válido para ese campo o no.

    `bool` es subclass de `int` en Python; el JSON `true/false` no debe
    pasar como duración numérica (sería 0/1 sin sentido). Por eso
    descartamos bools explícitamente al principio (antes del check
    `isinstance(raw, int)`).

    Centralizado acá para que el loader (`infra/workflow_loader._parse_steps`)
    y la UI (`ui/screens/workflow_summary._render_steps_block`) usen el
    MISMO criterio cuando normalizan el campo `duration_seconds` del
    JSON crudo — sin esto la preview del summary podía diverger del
    runtime ante valores tipo `"10"` (string numérico).
    """
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return None
        try:
            return int(stripped)
        except ValueError:
            return None
    # Tipos no convertibles (list, dict, etc.): no es un número.
    return None


# Mapping (key requerida según tipo de step) ⇒ keys que `progress` debe tener.
# Las extras no son error (futuro-proof), las faltantes sí (incompletitud).
_REQUIRED_PROGRESS_KEYS_A_ROLL: Final[frozenset[WorkflowProgressKey]] = frozenset(
    {
        WorkflowProgressKey.SCENE_IMAGE,
        WorkflowProgressKey.AUDIO,
        WorkflowProgressKey.VIDEO,
        WorkflowProgressKey.DOWNLOAD,
    }
)
_REQUIRED_PROGRESS_KEYS_B_ROLL_WITH_TEXT: Final[frozenset[WorkflowProgressKey]] = frozenset(
    {
        WorkflowProgressKey.SCENE_IMAGE,
        WorkflowProgressKey.AUDIO,
        WorkflowProgressKey.VIDEO,
        WorkflowProgressKey.DOWNLOAD_VIDEO,
        WorkflowProgressKey.DOWNLOAD_AUDIO,
    }
)
_REQUIRED_PROGRESS_KEYS_B_ROLL_SILENT: Final[frozenset[WorkflowProgressKey]] = frozenset(
    {
        WorkflowProgressKey.SCENE_IMAGE,
        WorkflowProgressKey.VIDEO,
        WorkflowProgressKey.DOWNLOAD,
    }
)

_WORKFLOW_SLUG_PATTERN: Final[re.Pattern[str]] = re.compile(r"[^a-z0-9]+")
_WORKFLOW_NAME_MAX_LEN: Final[int] = 120


def slugify_workflow_name(name: str) -> str:
    """Devuelve un slug filesystem-safe para usar en nombres de carpeta.

    No es reversible: dos nombres con caracteres distintos pueden colapsar
    al mismo slug. Eso está bien para el `WorkflowJob.slug` porque se
    combina con `id = wf_<timestamp>_<short_uuid>` para garantizar
    unicidad del `output_dir`. Si el nombre es vacío o solo símbolos,
    devuelve `"workflow"`.
    """
    lowered = name.strip().lower()
    cleaned = _WORKFLOW_SLUG_PATTERN.sub("_", lowered).strip("_")
    if not cleaned:
        return "workflow"
    return cleaned[:_WORKFLOW_NAME_MAX_LEN]


def validate_model_creation(model_creation: ModelCreation) -> None:
    """Valida la configuración de creación de la modelo base.

    Verifica que según `method` estén los campos requeridos. NO chequea
    existencia del path local en disco (eso es responsabilidad del
    `WorkflowRunner._resolve_base()` que revalida justo antes del upload
    para evitar race window).

    `method=local` SIN `local_path` (o con string vacío) es válido en el
    dominio: la UI puede pedírselo al usuario en runtime via selector de
    archivo. El controller exige el path solo si NO hay `resolved_image_ref`
    pre-cargada antes del enqueue.
    """
    method = model_creation.method
    if method == ModelCreationMethod.PROMPT:
        if not model_creation.prompt:
            raise WorkflowValidationError(
                "model_creation.method='prompt' requiere 'prompt' no vacío"
            )
        # Reusamos el validator de Nano Banana 2 — mismo modelo de generación.
        try:
            validate_image_prompt(model_creation.prompt)
        except ImageGenerationValidationError as exc:
            raise WorkflowValidationError(f"model_creation.prompt inválido: {exc}") from exc
    elif method == ModelCreationMethod.LOCAL:
        # local_path opcional acá: el selector de la UI lo completa al
        # encolar. Si llega seteado, la existencia se valida en el
        # `WorkflowBaseResolver.upload_local_standalone` o en
        # `WorkflowRunner._resolve_from_local` (revalidación pre-upload).
        return
    elif method == ModelCreationMethod.CATALOG:
        if model_creation.asset_kind is None or not model_creation.asset_id:
            raise WorkflowValidationError(
                "model_creation.method='catalog' requiere 'asset_kind' y 'asset_id' no vacíos"
            )


def validate_workflow_step(step: WorkflowStep) -> list[str]:
    """Valida un step y devuelve la lista de warnings (no bloqueantes).

    Errores estructurales se levantan como excepciones. Warnings se
    devuelven en una lista para que el caller los muestre en UI/loader
    sin bloquear ejecución.
    """
    if step.step < 1:
        raise WorkflowStepValidationError(f"step.step debe ser >= 1 (recibí {step.step})")
    if not step.scene_name.strip():
        raise WorkflowStepValidationError(f"step {step.step}: scene_name vacío")
    if not step.scene_slug.strip():
        raise WorkflowStepValidationError(f"step {step.step}: scene_slug vacío")
    if not step.prompt.strip():
        raise WorkflowStepValidationError(f"step {step.step}: prompt vacío")
    _validate_step_prompt_length(step)
    _validate_step_text_per_type(step)
    _validate_step_duration(step)
    if step.image_aspect_ratio is not None and step.image_aspect_ratio not in ASPECT_RATIOS:
        raise WorkflowStepValidationError(
            f"step {step.step}: image_aspect_ratio inválido: {step.image_aspect_ratio!r} "
            f"(válidos: {', '.join(ASPECT_RATIOS)})"
        )
    _validate_workflow_step_progress(step)
    return _collect_step_warnings(step)


def _validate_step_duration(step: WorkflowStep) -> None:
    """A-roll ignora `duration_seconds`. B-roll lo valida si está seteado.

    Si `duration_seconds` es `None`, el step usa el fallback global
    (pre_settings o settings). Si está seteado, debe ser un entero 3-15.

    Para a-roll, si el JSON trae `duration_seconds` lo dejamos pasar
    como warning visual del loader (la duración del avatar la define el
    audio TTS, no el setting), pero no es un error bloqueante.
    """
    if step.duration_seconds is None:
        return
    if step.type == StepType.B_ROLL:
        validate_i2v_duration(step.duration_seconds)


def _validate_step_prompt_length(step: WorkflowStep) -> None:
    """Aplica los límites de chars del prompt según `step.type`.

    A-roll usa Avatar Pro (límite del prompt = `MAX_PROMPT_CHARS=5000`).
    B-roll usa Kling i2v (límite del prompt = `MAX_I2V_PROMPT_CHARS=2500`).
    Si `change_scene=True`, el prompt también alimenta al Nano Banana
    refit con el `scene_description`, pero ese pasa por su propia
    validación en el runner.
    """
    max_chars = MAX_PROMPT_CHARS if step.type == StepType.A_ROLL else MAX_I2V_PROMPT_CHARS
    if len(step.prompt) > max_chars:
        raise WorkflowStepValidationError(f"step {step.step}: prompt supera {max_chars} caracteres")


def _validate_step_text_per_type(step: WorkflowStep) -> None:
    """A-roll exige `text` no vacío (hay que sincronizar audio). B-roll lo permite vacío."""
    if step.type == StepType.A_ROLL:
        if not step.text.strip():
            raise WorkflowStepValidationError(
                f"step {step.step}: tipo a-roll requiere 'text' no vacío "
                "(el audio se sincroniza con el video)"
            )
        try:
            validate_tts_script(step.text)
        except AudioValidationError as exc:
            raise WorkflowStepValidationError(
                f"step {step.step}: text inválido para TTS: {exc}"
            ) from exc
    elif step.text:
        # B-roll: si hay text (incluso solo whitespace), validamos como TTS.
        # Whitespace-only es error del JSON; "sin text" se expresa con "".
        try:
            validate_tts_script(step.text)
        except AudioValidationError as exc:
            raise WorkflowStepValidationError(
                f"step {step.step}: text inválido para TTS: {exc}"
            ) from exc


def _validate_workflow_step_progress(step: WorkflowStep) -> None:
    """Valida que `step.progress` tenga las keys correctas para `step.type`.

    Si `progress` está vacío, no chequea nada (default freshly-created).
    Si tiene keys, deben ser un subset de las esperadas para el tipo —
    sino indica corrupción/inconsistencia.
    """
    if not step.progress:
        return
    expected_keys = _expected_progress_keys(step)
    actual_keys = set(step.progress.keys())
    unexpected = actual_keys - expected_keys
    if unexpected:
        raise WorkflowStepValidationError(
            f"step {step.step}: progress tiene keys inválidas para tipo "
            f"{step.type.value}: {sorted(k.value for k in unexpected)}"
        )


def _expected_progress_keys(step: WorkflowStep) -> frozenset[WorkflowProgressKey]:
    """Devuelve el set de keys que `progress` debe (eventualmente) tener para este step.

    El criterio para b-roll considera `voiceover`:
    - `voiceover=true` Y `text` no vacío → ruta "with audio" (TTS aparte +
      video silencioso). Keys: SCENE_IMAGE, AUDIO, VIDEO, DOWNLOAD_VIDEO,
      DOWNLOAD_AUDIO.
    - `voiceover=true` Y `text` vacío → ruta "silent" (video silencioso solo).
      Keys: SCENE_IMAGE, VIDEO, DOWNLOAD.
    - `voiceover=false` → ruta "native sound" (Kling embebe sound efx en el
      video). Mismo set que silent: SCENE_IMAGE, VIDEO, DOWNLOAD.
    """
    if step.type == StepType.A_ROLL:
        return _REQUIRED_PROGRESS_KEYS_A_ROLL
    if step.voiceover and step.text.strip():
        return _REQUIRED_PROGRESS_KEYS_B_ROLL_WITH_TEXT
    return _REQUIRED_PROGRESS_KEYS_B_ROLL_SILENT


def expected_progress_keys_for_step(step: WorkflowStep) -> frozenset[WorkflowProgressKey]:
    """API pública del helper para que el runner inicialice `progress` con defaults."""
    return _expected_progress_keys(step)


def _collect_step_warnings(step: WorkflowStep) -> list[str]:
    """Devuelve mensajes informativos no bloqueantes para el step."""
    warnings: list[str] = []
    if step.type == StepType.B_ROLL and not step.change_scene:
        warnings.append(
            f"step {step.step}: b-roll con change_scene=false usará la imagen base "
            "de la modelo; normalmente querés change_scene=true para escenas auxiliares"
        )
    if step.type == StepType.B_ROLL and step.change_scene and not step.scene_description.strip():
        warnings.append(
            f"step {step.step}: change_scene=true pero scene_description vacío; "
            "la imagen scene se generará solo con el prompt del step"
        )
    if step.type == StepType.A_ROLL and step.duration_seconds is not None:
        warnings.append(
            f"step {step.step}: a-roll trae duration_seconds={step.duration_seconds}, "
            "pero la duración del avatar la determina el audio TTS. "
            "Este campo se ignora para a-roll; sacalo del JSON para evitar confusión."
        )
    # Voiceover: solo aplica a b-roll. Avisar combinaciones que se ignoran.
    if step.type == StepType.A_ROLL and step.voiceover is False:
        warnings.append(
            f"step {step.step}: a-roll trae voiceover=false, pero a-roll siempre "
            "tiene audio embebido del lip-sync. Este campo se ignora; sacalo del JSON."
        )
    if step.type == StepType.B_ROLL and step.voiceover is False and step.text.strip():
        warnings.append(
            f"step {step.step}: b-roll con voiceover=false ignora el campo 'text' "
            "(Kling 3.0 genera sound effects basados en el prompt, no en text). "
            "Si querés voz humana, usá voiceover=true."
        )
    if step.include_product and not step.product_prompt.strip():
        warnings.append(
            f"step {step.step}: include_product=true pero product_prompt vacío; "
            "Nano Banana compondrá el producto solo con el prompt de la escena. "
            "Agregá product_prompt para indicar cómo/dónde colocar el producto."
        )
    return warnings


def validate_workflow(workflow: WorkflowJob) -> list[str]:
    """Valida un `WorkflowJob` completo y devuelve la lista de warnings.

    Errores estructurales (steps vacío, numeros no consecutivos,
    model_creation inválido, step inválido) se levantan como excepciones.
    Warnings de los steps se agregan a la lista devuelta para que la UI
    los muestre.
    """
    if not workflow.name.strip():
        raise WorkflowValidationError("workflow.name no puede estar vacío")
    if not workflow.steps:
        raise WorkflowValidationError("workflow debe tener al menos 1 step")
    validate_model_creation(workflow.pre_settings.model_creation)
    if workflow.pre_settings.i2v_duration_seconds is not None:
        validate_i2v_duration(workflow.pre_settings.i2v_duration_seconds)
    if workflow.pre_settings.image_aspect_ratio is not None and workflow.pre_settings.image_aspect_ratio not in ASPECT_RATIOS:
        raise WorkflowValidationError(
            f"image_aspect_ratio inválido: {workflow.pre_settings.image_aspect_ratio!r} "
            f"(válidos: {', '.join(ASPECT_RATIOS)})"
        )
    _validate_workflow_step_numbering(workflow)
    warnings: list[str] = []
    warnings.extend(_validate_product_promotion(workflow))
    for step in workflow.steps:
        warnings.extend(validate_workflow_step(step))
    return warnings


def _validate_product_promotion(workflow: WorkflowJob) -> list[str]:
    """Valida la coherencia entre `promote_product` y los `include_product`.

    Error si algún step pide `include_product=true` pero el workflow no
    promociona producto (`promote_product=false`): no hay imagen de
    producto que componer. Warning si `promote_product=true` pero ningún
    step lo incluye (producto subido pero sin usar).
    """
    promote = workflow.pre_settings.promote_product
    steps_with_product = [step.step for step in workflow.steps if step.include_product]
    if steps_with_product and not promote:
        raise WorkflowValidationError(
            f"steps {steps_with_product} tienen include_product=true pero "
            "pre_settings.promote_product=false; activá promote_product y elegí "
            "el producto, o quitá include_product de esos steps"
        )
    if promote and not steps_with_product:
        return [
            "promote_product=true pero ningún step tiene include_product=true; "
            "el producto se subirá pero no aparecerá en ninguna escena"
        ]
    return []


def _validate_workflow_step_numbering(workflow: WorkflowJob) -> None:
    """Los steps deben numerarse consecutivamente desde 1 sin gaps ni duplicados."""
    expected = list(range(1, len(workflow.steps) + 1))
    actual = sorted(step.step for step in workflow.steps)
    if actual != expected:
        raise WorkflowValidationError(
            f"workflow.steps deben numerarse de 1 a {len(workflow.steps)} "
            f"consecutivos (recibí {actual})"
        )


def validate_i2v_duration(duration: int) -> None:
    """Valida `duration` del endpoint Kling 3.0 video (`kling-3.0/video`)."""
    if duration not in I2V_DURATIONS:
        raise WorkflowStepValidationError(
            f"duration i2v inválido: {duration} (válidos: {', '.join(map(str, I2V_DURATIONS))})"
        )
