from pathlib import Path

import pytest

from kie_avatar_studio.domain.errors import (
    JobValidationError,
    KeyValidationError,
    UrlValidationError,
)
from kie_avatar_studio.domain.models import VideoJob
from kie_avatar_studio.domain.policies import (
    MAX_IMAGE_BYTES,
    MAX_PROMPT_CHARS,
    MAX_SCRIPT_CHARS,
    MIN_KEY_LENGTH,
    extract_result_url,
    is_path_inside,
    normalize_task_status,
    validate_job,
    validate_key_label,
    validate_kie_key,
)


def _make_job(tmp_path: Path, **overrides) -> VideoJob:
    image = tmp_path / "modelo.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    base = dict(
        id="job_1",
        script="hola",
        image_path=str(image),
        prompt="prompt valido",
        voice="V",
    )
    base.update(overrides)
    return VideoJob(**base)


def test_validate_job_happy_path(tmp_path: Path) -> None:
    validate_job(_make_job(tmp_path))


def test_validate_job_rejects_empty_script(tmp_path: Path) -> None:
    with pytest.raises(JobValidationError, match="script vacío"):
        validate_job(_make_job(tmp_path, script=""))


def test_validate_job_rejects_long_script(tmp_path: Path) -> None:
    with pytest.raises(JobValidationError, match="script supera"):
        validate_job(_make_job(tmp_path, script="x" * (MAX_SCRIPT_CHARS + 1)))


def test_validate_job_rejects_long_prompt(tmp_path: Path) -> None:
    with pytest.raises(JobValidationError, match="prompt supera"):
        validate_job(_make_job(tmp_path, prompt="x" * (MAX_PROMPT_CHARS + 1)))


def test_validate_job_rejects_missing_image(tmp_path: Path) -> None:
    with pytest.raises(JobValidationError, match="imagen no encontrada"):
        validate_job(_make_job(tmp_path, image_path=str(tmp_path / "no.png")))


def test_validate_job_rejects_bad_extension(tmp_path: Path) -> None:
    bad = tmp_path / "modelo.bmp"
    bad.write_bytes(b"\x00")
    with pytest.raises(JobValidationError, match="formato"):
        validate_job(_make_job(tmp_path, image_path=str(bad)))


def test_validate_job_rejects_oversized_image(tmp_path: Path) -> None:
    big = tmp_path / "big.png"
    # archivo sparse de tamaño > 10 MB sin escribir los bytes reales
    with big.open("wb") as fp:
        fp.truncate(MAX_IMAGE_BYTES + 1)
    with pytest.raises(JobValidationError, match="MB"):
        validate_job(_make_job(tmp_path, image_path=str(big)))


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, "pending"),
        ("", "pending"),
        ("PENDING", "pending"),
        ("queued", "pending"),
        ("running", "running"),
        ("Processing", "running"),
        ("success", "success"),
        ("SUCCEEDED", "success"),
        ("done", "success"),
        ("failed", "failed"),
        ("ERROR", "failed"),
        ("weird-unknown", "running"),  # default conservador
    ],
)
def test_normalize_task_status(raw, expected) -> None:
    assert normalize_task_status(raw) == expected


def test_extract_result_url_prefers_known_keys() -> None:
    assert (
        extract_result_url({"data": {"audio_url": "https://a", "result_url": "https://r"}})
        == "https://a"
    )
    assert extract_result_url({"data": {"video_url": "https://v"}}) == "https://v"
    assert extract_result_url({"data": {"output": {"url": "https://o"}}}) == "https://o"
    assert extract_result_url({"data": {}}) is None
    assert extract_result_url({}) is None
    assert extract_result_url({"data": None}) is None


def test_is_path_inside(tmp_path: Path) -> None:
    safe = tmp_path / "outputs" / "job" / "final.mp4"
    safe.parent.mkdir(parents=True)
    safe.write_bytes(b"")
    assert is_path_inside(safe, tmp_path / "outputs")
    assert not is_path_inside(tmp_path / "outputs" / ".." / "etc", tmp_path / "outputs")


# --- validate_kie_key / validate_key_label -----------------------------------


def test_validate_kie_key_happy_path() -> None:
    validate_kie_key("sk-1234567890abcd")


def test_validate_kie_key_rejects_empty() -> None:
    with pytest.raises(KeyValidationError, match="vacía"):
        validate_kie_key("")


def test_validate_kie_key_rejects_whitespace_around() -> None:
    with pytest.raises(KeyValidationError, match="espacios alrededor"):
        validate_kie_key("  sk-12345678  ")


def test_validate_kie_key_rejects_too_short() -> None:
    with pytest.raises(KeyValidationError, match=str(MIN_KEY_LENGTH)):
        validate_kie_key("short")


def test_validate_kie_key_rejects_internal_whitespace() -> None:
    with pytest.raises(KeyValidationError, match="espacios internos"):
        validate_kie_key("sk-1234 567890")


def test_validate_key_label_happy_path() -> None:
    validate_key_label("Cuenta personal")
    validate_key_label("dev-prod_42.test")


def test_validate_key_label_rejects_empty() -> None:
    with pytest.raises(KeyValidationError, match="vacío"):
        validate_key_label("   ")


def test_validate_key_label_rejects_special_chars() -> None:
    with pytest.raises(KeyValidationError, match="admite"):
        validate_key_label("hola!mundo")


# --- validate_http_url -------------------------------------------------------


def test_validate_http_url_accepts_http() -> None:
    from kie_avatar_studio.domain.policies import validate_http_url

    validate_http_url("http://example.com/foo")


def test_validate_http_url_accepts_https() -> None:
    from kie_avatar_studio.domain.policies import validate_http_url

    validate_http_url("https://tempfile.redpandaai.co/kieai/abc/modelo.png")


def test_validate_http_url_rejects_empty() -> None:
    from kie_avatar_studio.domain.policies import validate_http_url

    with pytest.raises(UrlValidationError, match="vacía"):
        validate_http_url("")


def test_validate_http_url_rejects_whitespace_around() -> None:
    from kie_avatar_studio.domain.policies import validate_http_url

    with pytest.raises(UrlValidationError, match="espacios alrededor"):
        validate_http_url("  https://x  ")


def test_validate_http_url_rejects_internal_whitespace() -> None:
    from kie_avatar_studio.domain.policies import validate_http_url

    with pytest.raises(UrlValidationError, match="espacios internos"):
        validate_http_url("https://example .com")


def test_validate_http_url_rejects_file_scheme() -> None:
    from kie_avatar_studio.domain.policies import validate_http_url

    with pytest.raises(UrlValidationError, match="http://"):
        validate_http_url("file:///etc/passwd")


def test_validate_http_url_rejects_javascript_scheme() -> None:
    from kie_avatar_studio.domain.policies import validate_http_url

    with pytest.raises(UrlValidationError, match="http://"):
        validate_http_url("javascript:alert(1)")


def test_validate_http_url_rejects_no_scheme() -> None:
    from kie_avatar_studio.domain.policies import validate_http_url

    with pytest.raises(UrlValidationError, match="http://"):
        validate_http_url("tempfile.redpandaai.co/x.png")


# --- TTS / voice (Fase 2.2c) -----------------------------------------------


def test_validate_tts_script_happy_path() -> None:
    from kie_avatar_studio.domain.policies import validate_tts_script

    validate_tts_script("hola mundo")


def test_validate_tts_script_rejects_empty() -> None:
    from kie_avatar_studio.domain.errors import AudioValidationError
    from kie_avatar_studio.domain.policies import validate_tts_script

    with pytest.raises(AudioValidationError, match="vacío"):
        validate_tts_script("")


def test_validate_tts_script_rejects_whitespace_only() -> None:
    from kie_avatar_studio.domain.errors import AudioValidationError
    from kie_avatar_studio.domain.policies import validate_tts_script

    with pytest.raises(AudioValidationError, match="vacío"):
        validate_tts_script("   ")


def test_validate_tts_script_rejects_too_long() -> None:
    from kie_avatar_studio.domain.errors import AudioValidationError
    from kie_avatar_studio.domain.policies import validate_tts_script

    with pytest.raises(AudioValidationError, match="supera"):
        validate_tts_script("x" * (MAX_SCRIPT_CHARS + 1))


def test_validate_voice_id_accepts_builtin() -> None:
    from kie_avatar_studio.domain.policies import validate_voice_id

    validate_voice_id("EkK5I93UQWFDigLMpZcX", allow_custom=False)


def test_validate_voice_id_accepts_custom_when_allowed() -> None:
    from kie_avatar_studio.domain.policies import validate_voice_id

    validate_voice_id("voice-id-de-cuenta-pro", allow_custom=True)


def test_validate_voice_id_rejects_custom_when_disallowed() -> None:
    from kie_avatar_studio.domain.errors import AudioValidationError
    from kie_avatar_studio.domain.policies import validate_voice_id

    with pytest.raises(AudioValidationError, match="built-in"):
        validate_voice_id("voice-id-inventado", allow_custom=False)


def test_validate_voice_id_rejects_empty() -> None:
    from kie_avatar_studio.domain.errors import AudioValidationError
    from kie_avatar_studio.domain.policies import validate_voice_id

    with pytest.raises(AudioValidationError, match="vacío"):
        validate_voice_id("")


def test_validate_voice_id_rejects_internal_whitespace() -> None:
    from kie_avatar_studio.domain.errors import AudioValidationError
    from kie_avatar_studio.domain.policies import validate_voice_id

    with pytest.raises(AudioValidationError, match="espacios"):
        validate_voice_id("voice id")


def test_validate_voice_id_rejects_too_short() -> None:
    from kie_avatar_studio.domain.errors import AudioValidationError
    from kie_avatar_studio.domain.policies import validate_voice_id

    with pytest.raises(AudioValidationError, match="al menos"):
        validate_voice_id("ab")


def test_validate_voice_settings_accepts_none_language() -> None:
    from kie_avatar_studio.domain.models import VoiceSettings
    from kie_avatar_studio.domain.policies import validate_voice_settings

    validate_voice_settings(VoiceSettings(stability=0.5))


def test_validate_voice_settings_accepts_valid_iso_code() -> None:
    from kie_avatar_studio.domain.models import VoiceSettings
    from kie_avatar_studio.domain.policies import validate_voice_settings

    validate_voice_settings(VoiceSettings(language_code="es"))
    validate_voice_settings(VoiceSettings(language_code="EN"))  # case-insensitive


def test_validate_voice_settings_rejects_bad_language_code() -> None:
    from kie_avatar_studio.domain.errors import VoiceSettingsValidationError
    from kie_avatar_studio.domain.models import VoiceSettings
    from kie_avatar_studio.domain.policies import validate_voice_settings

    with pytest.raises(VoiceSettingsValidationError, match="ISO 639-1"):
        validate_voice_settings(VoiceSettings(language_code="español"))


def test_validate_voice_settings_accepts_empty_language_code() -> None:
    """Empty string es válido (equivale a no setear)."""
    from kie_avatar_studio.domain.models import VoiceSettings
    from kie_avatar_studio.domain.policies import validate_voice_settings

    validate_voice_settings(VoiceSettings(language_code=""))


# --- Shape real de recordInfo (Fase 2.2c.fix shape) -----------------------


def _real_tts_success_payload() -> dict:
    """Payload exacto observado contra Kie real para un TTS exitoso."""
    return {
        "code": 200,
        "msg": "success",
        "data": {
            "taskId": "6eeeb536138cf2c8ddac9a328620997b",
            "model": "elevenlabs/text-to-speech-multilingual-v2",
            "state": "success",
            "param": '{"input":"{...}","model":"elevenlabs/text-to-speech-multilingual-v2"}',
            "resultJson": ('{"resultUrls":["https://tempfile.aiquickdraw.com/voice/abc_123.mp3"]}'),
            "failCode": None,
            "failMsg": None,
            "costTime": 26,
            "completeTime": 1780450222119,
            "createTime": 1780450195474,
            "creditsConsumed": 12.0,
        },
    }


def test_extract_task_status_reads_state_field() -> None:
    """Kie real usa `state`, no `status`. Antes esto devolvía 'running' siempre."""
    from kie_avatar_studio.domain.policies import extract_task_status

    payload = _real_tts_success_payload()
    assert extract_task_status(payload) == "success"


def test_extract_task_status_running_when_pending() -> None:
    """Mientras Kie procesa, devuelve state vacío o pending."""
    from kie_avatar_studio.domain.policies import extract_task_status

    payload = {"data": {"state": "waiting"}}
    assert extract_task_status(payload) == "pending"


def test_extract_task_status_falls_back_to_status_field() -> None:
    """Compatibilidad con shape viejo que usaba `status`."""
    from kie_avatar_studio.domain.policies import extract_task_status

    payload = {"data": {"status": "running"}}
    assert extract_task_status(payload) == "running"


def test_extract_task_status_unknown_assumes_running() -> None:
    """Sin campo de estado reconocido, asumimos running (más seguro que failed)."""
    from kie_avatar_studio.domain.policies import extract_task_status

    payload = {"data": {"otherField": "x"}}
    assert extract_task_status(payload) == "running"


def test_extract_task_status_fail_synonyms() -> None:
    from kie_avatar_studio.domain.policies import extract_task_status

    assert extract_task_status({"data": {"state": "fail"}}) == "failed"
    assert extract_task_status({"data": {"state": "failed"}}) == "failed"
    assert extract_task_status({"data": {"state": "error"}}) == "failed"


def test_extract_result_url_parses_result_json() -> None:
    """Shape real: `data.resultJson` (string) → `resultUrls[0]`."""
    from kie_avatar_studio.domain.policies import extract_result_url

    payload = _real_tts_success_payload()
    url = extract_result_url(payload)
    assert url == "https://tempfile.aiquickdraw.com/voice/abc_123.mp3"


def test_extract_result_url_handles_malformed_result_json() -> None:
    """Si el resultJson no es parseable, no debe propagar JSONDecodeError."""
    from kie_avatar_studio.domain.policies import extract_result_url

    payload = {"data": {"resultJson": "{not valid json"}}
    assert extract_result_url(payload) is None


def test_extract_result_url_prefers_result_json_over_legacy_keys() -> None:
    """Si conviven ambos shapes, gana resultJson (forma actual)."""
    from kie_avatar_studio.domain.policies import extract_result_url

    payload = {
        "data": {
            "resultJson": '{"resultUrls":["https://new.mp3"]}',
            "audio_url": "https://old.mp3",
        }
    }
    assert extract_result_url(payload) == "https://new.mp3"


def test_extract_failure_message_from_fail_msg() -> None:
    from kie_avatar_studio.domain.policies import extract_failure_message

    payload = {"data": {"state": "failed", "failMsg": "voice not found"}}
    assert extract_failure_message(payload) == "voice not found"


def test_extract_failure_message_returns_none_on_success() -> None:
    """Sobre un payload de éxito, no debe inventar un mensaje."""
    from kie_avatar_studio.domain.policies import extract_failure_message

    assert extract_failure_message(_real_tts_success_payload()) is None
