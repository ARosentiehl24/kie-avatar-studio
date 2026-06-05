from datetime import UTC, datetime, timedelta
from pathlib import Path

from kie_avatar_studio.domain.models import UploadedImage


def test_default_fields() -> None:
    img = UploadedImage(
        id="modelo",
        label="modelo principal",
        local_path="/tmp/modelo.png",
        kie_url="https://tempfile.redpandaai.co/x.png",
        kie_file_path="kieai/x.png",
        file_size=12345,
        mime_type="image/png",
    )
    assert img.id == "modelo"
    assert img.file_size == 12345
    assert isinstance(img.uploaded_at, datetime)


def test_local_file_exists_true(tmp_path: Path) -> None:
    actual = tmp_path / "img.png"
    actual.write_bytes(b"PNG")
    img = UploadedImage(
        id="x",
        label="x",
        local_path=str(actual),
        kie_url="https://x",
        kie_file_path="x",
        file_size=3,
        mime_type="image/png",
    )
    assert img.local_file_exists() is True


def test_local_file_exists_false() -> None:
    img = UploadedImage(
        id="x",
        label="x",
        local_path="/tmp/no_existe_jamas.png",
        kie_url="https://x",
        kie_file_path="x",
        file_size=1,
        mime_type="image/png",
    )
    assert img.local_file_exists() is False


def test_serialization_roundtrip(tmp_path: Path) -> None:
    original = UploadedImage(
        id="m",
        label="m",
        local_path=str(tmp_path / "img.png"),
        kie_url="https://x",
        kie_file_path="x",
        file_size=99,
        mime_type="image/jpeg",
    )
    restored = UploadedImage.model_validate_json(original.model_dump_json())
    assert restored == original


# --- Expiración derivada de uploaded_at + retention --------------------------


def _img(uploaded_at: datetime) -> UploadedImage:
    return UploadedImage(
        id="x",
        label="x",
        local_path="/tmp/x.png",
        kie_url="https://x",
        kie_file_path="x",
        file_size=1,
        mime_type="image/png",
        uploaded_at=uploaded_at,
    )


def test_expires_at_adds_retention_hours() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    img = _img(now)
    assert img.expires_at(24) == now + timedelta(hours=24)


def test_is_expired_true_after_retention_window() -> None:
    uploaded = datetime(2026, 1, 1, tzinfo=UTC)
    img = _img(uploaded)
    later = uploaded + timedelta(hours=25)
    assert img.is_expired(24, now=later) is True


def test_is_expired_false_within_window() -> None:
    uploaded = datetime(2026, 1, 1, tzinfo=UTC)
    img = _img(uploaded)
    later = uploaded + timedelta(hours=23, minutes=59)
    assert img.is_expired(24, now=later) is False


def test_time_left_positive_within_window() -> None:
    uploaded = datetime(2026, 1, 1, tzinfo=UTC)
    img = _img(uploaded)
    later = uploaded + timedelta(hours=10)
    assert img.time_left(24, now=later) == timedelta(hours=14)


def test_time_left_negative_after_expiry() -> None:
    uploaded = datetime(2026, 1, 1, tzinfo=UTC)
    img = _img(uploaded)
    later = uploaded + timedelta(hours=48)
    delta = img.time_left(24, now=later)
    assert delta.total_seconds() < 0
    assert delta == timedelta(hours=-24)
