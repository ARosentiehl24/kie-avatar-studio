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
    ImageValidationError,
    JobValidationError,
    KeyValidationError,
    UrlValidationError,
    VoiceSettingsValidationError,
)
from .kie_voice_catalog import is_builtin_voice
from .models import VideoJob, VoiceSettings

MAX_SCRIPT_CHARS: Final[int] = 5000
MAX_PROMPT_CHARS: Final[int] = 5000
_BYTES_PER_MB: Final[int] = 1024 * 1024
MAX_IMAGE_BYTES: Final[int] = 10 * _BYTES_PER_MB
MAX_AUDIO_BYTES: Final[int] = 100 * _BYTES_PER_MB
MAX_AUDIO_SECONDS: Final[int] = 5 * 60

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
# Subset razonable de códigos ISO 639-1. Validamos solo formato (2 letras),
# Kie/ElevenLabs valida el código real.
_LANGUAGE_CODE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z]{2}$")


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
                "language_code debe ser un código ISO 639-1 de 2 letras (ej: 'es', 'en')"
            )
