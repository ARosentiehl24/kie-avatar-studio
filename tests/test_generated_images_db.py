"""Tests de `GeneratedImagesDB` (persistencia de imágenes generadas por Kie)."""

from __future__ import annotations

import pytest

from kie_avatar_studio.domain.errors import GeneratedImageNotFoundError
from kie_avatar_studio.domain.models import GeneratedImage, ImageGenerationSettings
from kie_avatar_studio.infra.generated_images_db import GeneratedImagesDB


@pytest.fixture
async def db(tmp_path) -> GeneratedImagesDB:
    d = GeneratedImagesDB(tmp_path / "jobs.db")
    await d.init()
    return d


def _sample(image_id: str = "img_gen_1", **kwargs) -> GeneratedImage:
    base = {
        "id": image_id,
        "label": "Paisaje",
        "prompt": "atardecer",
        "kie_url": "https://tempfile.redpandaai.co/" + image_id + ".png",
        "kie_file_path": "imgs/" + image_id + ".png",
    }
    base.update(kwargs)
    return GeneratedImage(**base)


async def test_init_idempotent(tmp_path) -> None:
    d = GeneratedImagesDB(tmp_path / "jobs.db")
    await d.init()
    await d.init()
    assert await d.list_recent() == []


async def test_upsert_with_settings_roundtrip(db: GeneratedImagesDB) -> None:
    settings = ImageGenerationSettings(aspect_ratio="16:9", resolution="2K", output_format="png")
    image = _sample(settings=settings, refs_count=3, file_size=1024, mime_type="image/png")
    await db.upsert(image)
    fetched = await db.get(image.id)
    assert fetched is not None
    assert fetched.settings is not None
    assert fetched.settings.aspect_ratio == "16:9"
    assert fetched.settings.resolution == "2K"
    assert fetched.refs_count == 3
    assert fetched.file_size == 1024
    assert fetched.mime_type == "image/png"


async def test_upsert_without_settings_keeps_null(db: GeneratedImagesDB) -> None:
    image = _sample(settings=None)
    await db.upsert(image)
    fetched = await db.get(image.id)
    assert fetched is not None
    assert fetched.settings is None
    assert fetched.refs_count == 0
    assert fetched.file_size is None
    assert fetched.mime_type is None


async def test_upsert_updates_existing(db: GeneratedImagesDB) -> None:
    image = _sample(label="v1")
    await db.upsert(image)
    image.label = "v2"
    image.refs_count = 5
    await db.upsert(image)
    fetched = await db.get(image.id)
    assert fetched is not None
    assert fetched.label == "v2"
    assert fetched.refs_count == 5


async def test_list_recent_ordered_desc(db: GeneratedImagesDB) -> None:
    a = _sample("img_a")
    await db.upsert(a)
    b = _sample("img_b")
    await db.upsert(b)
    listed = await db.list_recent()
    assert [i.id for i in listed] == ["img_b", "img_a"]


async def test_delete_existing_ok(db: GeneratedImagesDB) -> None:
    image = _sample()
    await db.upsert(image)
    await db.delete(image.id)
    assert await db.get(image.id) is None


async def test_delete_missing_raises(db: GeneratedImagesDB) -> None:
    with pytest.raises(GeneratedImageNotFoundError):
        await db.delete("never_existed")


async def test_delete_many_idempotent(db: GeneratedImagesDB) -> None:
    a = _sample("img_a")
    b = _sample("img_b")
    await db.upsert(a)
    await db.upsert(b)
    # Incluye uno que no existe — debe quitar los que sí y no lanzar.
    await db.delete_many([a.id, "ghost", b.id])
    assert await db.list_recent() == []


async def test_delete_many_empty_noop(db: GeneratedImagesDB) -> None:
    a = _sample()
    await db.upsert(a)
    await db.delete_many([])
    assert await db.get(a.id) is not None
