from datetime import UTC, datetime
from pathlib import Path

import pytest

from kie_avatar_studio.domain.errors import ImageNotFoundError
from kie_avatar_studio.domain.models import UploadedImage
from kie_avatar_studio.infra.images_db import ImagesDB


@pytest.fixture
async def db(tmp_path: Path) -> ImagesDB:
    d = ImagesDB(tmp_path / "jobs.db")
    await d.init()
    return d


def _img(image_id: str = "modelo", label: str = "modelo principal") -> UploadedImage:
    return UploadedImage(
        id=image_id,
        label=label,
        local_path=f"/tmp/{image_id}.png",
        kie_url=f"https://tempfile.redpandaai.co/{image_id}.png",
        kie_file_path=f"kieai/{image_id}.png",
        file_size=12345,
        mime_type="image/png",
    )


async def test_init_creates_schema(db: ImagesDB) -> None:
    await db.upsert(_img())
    fetched = await db.get("modelo")
    assert fetched is not None


async def test_get_missing_returns_none(db: ImagesDB) -> None:
    assert await db.get("ghost") is None


async def test_list_recent_orders_desc(db: ImagesDB) -> None:
    await db.upsert(_img("a"))
    # Forzamos uploaded_at distinto para verificar orden.
    later = _img("b").model_copy(update={"uploaded_at": datetime.now(UTC)})
    await db.upsert(later)
    listed = await db.list_recent()
    assert listed[0].id == "b"  # más reciente primero


async def test_delete_existing(db: ImagesDB) -> None:
    await db.upsert(_img())
    await db.delete("modelo")
    assert await db.get("modelo") is None


async def test_delete_missing_raises(db: ImagesDB) -> None:
    with pytest.raises(ImageNotFoundError):
        await db.delete("ghost")


async def test_upsert_updates_existing(db: ImagesDB) -> None:
    await db.upsert(_img(label="original"))
    await db.upsert(_img(label="renombrado"))
    fetched = await db.get("modelo")
    assert fetched is not None
    assert fetched.label == "renombrado"
    assert len(await db.list_recent()) == 1
