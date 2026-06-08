"""Tests del cleanup automático de imágenes expiradas en `ImagesController`.

Las imágenes subidas a Kie expiran en 24h (File Upload API). Antes el
controller usaba `retention_days` por error (heredado de cuando se asumía
que duraban 14d como los `GeneratedAudio`). El bug aparecía como
"Image fetch failed" en Kie al usar imágenes >24h en un avatar task.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from kie_avatar_studio.app_layer.images_controller import ImagesController
from kie_avatar_studio.domain.models import UploadedImage
from kie_avatar_studio.infra.images_db import ImagesDB
from kie_avatar_studio.infra.kie_client import KieClient


def _client_with_handler(tmp_settings, handler) -> KieClient:
    client = KieClient(tmp_settings)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": f"Bearer {tmp_settings.kie_api_key}"},
    )
    return client


def _img(image_id: str, uploaded_at: datetime) -> UploadedImage:
    return UploadedImage(
        id=image_id,
        label=image_id,
        local_path=f"/tmp/{image_id}.png",
        kie_url=f"https://x/{image_id}",
        kie_file_path=f"kieai/{image_id}.png",
        file_size=1,
        mime_type="image/png",
        uploaded_at=uploaded_at,
    )


@pytest.fixture
async def store(tmp_path: Path) -> ImagesDB:
    d = ImagesDB(tmp_path / "jobs.db")
    await d.init()
    return d


async def test_cleanup_expired_removes_only_expired(store: ImagesDB, tmp_settings) -> None:
    now = datetime.now(UTC)
    await store.upsert(_img("fresca", now - timedelta(hours=2)))
    await store.upsert(_img("vieja", now - timedelta(hours=48)))
    await store.upsert(_img("limite", now - timedelta(hours=24, seconds=1)))

    client = _client_with_handler(tmp_settings, lambda r: httpx.Response(200))
    ctl = ImagesController(store, client, retention_hours=24)
    try:
        removed = await ctl.cleanup_expired()
    finally:
        await client.aclose()

    removed_ids = {img.id for img in removed}
    assert removed_ids == {"vieja", "limite"}
    remaining = await ctl.list_uploaded()
    assert {img.id for img in remaining} == {"fresca"}


async def test_cleanup_expired_idempotent(store: ImagesDB, tmp_settings) -> None:
    now = datetime.now(UTC)
    await store.upsert(_img("vieja", now - timedelta(hours=72)))

    client = _client_with_handler(tmp_settings, lambda r: httpx.Response(200))
    ctl = ImagesController(store, client, retention_hours=24)
    try:
        first = await ctl.cleanup_expired()
        second = await ctl.cleanup_expired()
    finally:
        await client.aclose()

    assert len(first) == 1
    assert second == []


async def test_cleanup_expired_with_no_images_returns_empty(store: ImagesDB, tmp_settings) -> None:
    client = _client_with_handler(tmp_settings, lambda r: httpx.Response(200))
    ctl = ImagesController(store, client)
    try:
        result = await ctl.cleanup_expired()
    finally:
        await client.aclose()
    assert result == []


async def test_controller_exposes_retention_hours(store: ImagesDB, tmp_settings) -> None:
    client = _client_with_handler(tmp_settings, lambda r: httpx.Response(200))
    ctl = ImagesController(store, client, retention_hours=12)
    assert ctl.retention_hours == 12
    await client.aclose()


async def test_cleanup_respects_custom_retention_hours(store: ImagesDB, tmp_settings) -> None:
    """Si alguien cablea retention_hours custom, el corte se mueve."""
    now = datetime.now(UTC)
    await store.upsert(_img("hace_6h", now - timedelta(hours=6)))
    await store.upsert(_img("hace_18h", now - timedelta(hours=18)))

    client = _client_with_handler(tmp_settings, lambda r: httpx.Response(200))
    ctl = ImagesController(store, client, retention_hours=12)
    try:
        removed = await ctl.cleanup_expired()
    finally:
        await client.aclose()

    # Con 12h de retención: "hace_18h" expiró, "hace_6h" no.
    assert {img.id for img in removed} == {"hace_18h"}


async def test_default_retention_is_24_hours(store: ImagesDB, tmp_settings) -> None:
    """Regresión del bug que causaba 'Image fetch failed': por defecto, Kie
    expira los uploads tras 24h, no 14 días."""
    from kie_avatar_studio.domain.policies import KIE_UPLOAD_RETENTION_HOURS

    assert KIE_UPLOAD_RETENTION_HOURS == 24
    client = _client_with_handler(tmp_settings, lambda r: httpx.Response(200))
    ctl = ImagesController(store, client)
    assert ctl.retention_hours == 24
    await client.aclose()


# --- get_for_use: rechaza expiradas o inexistentes -----------------------


async def test_get_for_use_returns_fresh_image(store: ImagesDB, tmp_settings) -> None:
    """Imagen dentro de la ventana de retención se devuelve normalmente."""
    now = datetime.now(UTC)
    await store.upsert(_img("fresca", now - timedelta(hours=2)))
    client = _client_with_handler(tmp_settings, lambda r: httpx.Response(200))
    ctl = ImagesController(store, client, retention_hours=24)
    try:
        image = await ctl.get_for_use("fresca")
    finally:
        await client.aclose()
    assert image.id == "fresca"


async def test_get_for_use_raises_not_found(store: ImagesDB, tmp_settings) -> None:
    from kie_avatar_studio.domain.errors import ImageNotFoundError

    client = _client_with_handler(tmp_settings, lambda r: httpx.Response(200))
    ctl = ImagesController(store, client)
    try:
        with pytest.raises(ImageNotFoundError):
            await ctl.get_for_use("no-existe")
    finally:
        await client.aclose()


async def test_get_for_use_raises_expired(store: ImagesDB, tmp_settings) -> None:
    """Imagen pasada del TTL lanza ImageExpiredError con mensaje claro."""
    from kie_avatar_studio.domain.errors import ImageExpiredError

    now = datetime.now(UTC)
    await store.upsert(_img("vieja", now - timedelta(hours=48)))
    client = _client_with_handler(tmp_settings, lambda r: httpx.Response(200))
    ctl = ImagesController(store, client, retention_hours=24)
    try:
        with pytest.raises(ImageExpiredError, match="expiró en Kie"):
            await ctl.get_for_use("vieja")
    finally:
        await client.aclose()


async def test_get_for_use_expired_message_includes_label(store: ImagesDB, tmp_settings) -> None:
    """El mensaje de error usa el label legible para que el usuario sepa cuál."""
    from kie_avatar_studio.domain.errors import ImageExpiredError

    now = datetime.now(UTC)
    image = _img("vieja", now - timedelta(hours=48)).model_copy(
        update={"label": "modelo principal"}
    )
    await store.upsert(image)
    client = _client_with_handler(tmp_settings, lambda r: httpx.Response(200))
    ctl = ImagesController(store, client, retention_hours=24)
    try:
        with pytest.raises(ImageExpiredError, match="modelo principal"):
            await ctl.get_for_use("vieja")
    finally:
        await client.aclose()


def test_format_time_left_renderiza_horas_y_minutos() -> None:
    """Con retención de 24h, el formato típico es 'Xh Ym'.

    `format_time_left` ahora vive en `ui/screens/_image_format.py`
    (extraído de `images.py` por CR-3.2).
    """
    from kie_avatar_studio.ui.screens._image_format import format_time_left

    assert format_time_left(timedelta(hours=2, minutes=30)) == "2h 30m"
    assert format_time_left(timedelta(hours=23, minutes=15)) == "23h 15m"
    assert format_time_left(timedelta(seconds=0)) == "EXPIRADO"
    assert format_time_left(timedelta(hours=-1)) == "EXPIRADO"
    # Backwards compat: si por algún motivo se pasa >1 día, sigue
    # formateando "Xd Yh" (test_audios usa el mismo helper).
    assert format_time_left(timedelta(days=12, hours=4)) == "12d 4h"
