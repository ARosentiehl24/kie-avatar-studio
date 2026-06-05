"""Tests de `ImageCatalogController` (facade mixto uploaded + generated)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kie_avatar_studio.app_layer.image_catalog_controller import ImageCatalogController
from kie_avatar_studio.domain.errors import (
    GeneratedImageExpiredError,
    GeneratedImageNotFoundError,
    ImageExpiredError,
    ImageNotFoundError,
)
from kie_avatar_studio.domain.models import (
    GeneratedImage,
    ImageAssetKind,
    UploadedImage,
)
from kie_avatar_studio.infra.generated_images_db import GeneratedImagesDB
from kie_avatar_studio.infra.images_db import ImagesDB


@pytest.fixture
async def uploaded_store(tmp_path) -> ImagesDB:
    d = ImagesDB(tmp_path / "jobs.db")
    await d.init()
    return d


@pytest.fixture
async def generated_store(tmp_path) -> GeneratedImagesDB:
    d = GeneratedImagesDB(tmp_path / "jobs.db")
    await d.init()
    return d


@pytest.fixture
def catalog(uploaded_store, generated_store) -> ImageCatalogController:
    return ImageCatalogController(uploaded_store, generated_store)


def _uploaded(idx: int, *, age_hours: int = 0) -> UploadedImage:
    return UploadedImage(
        id=f"up_{idx}",
        label=f"up{idx}",
        local_path=f"/tmp/up{idx}.png",
        kie_url=f"https://tempfile.redpandaai.co/up{idx}.png",
        kie_file_path=f"kieai/up{idx}.png",
        file_size=100,
        mime_type="image/png",
        uploaded_at=datetime.now(UTC) - timedelta(hours=age_hours),
    )


def _generated(idx: int, *, age_days: int = 0) -> GeneratedImage:
    return GeneratedImage(
        id=f"gen_{idx}",
        label=f"gen{idx}",
        prompt="x",
        kie_url=f"https://tempfile.redpandaai.co/gen{idx}.png",
        kie_file_path=f"kieai/gen{idx}.png",
        generated_at=datetime.now(UTC) - timedelta(days=age_days),
    )


async def test_list_usable_includes_both_kinds(
    catalog: ImageCatalogController, uploaded_store: ImagesDB, generated_store: GeneratedImagesDB
) -> None:
    await uploaded_store.upsert(_uploaded(1))
    await generated_store.upsert(_generated(1))
    listed = await catalog.list_usable_assets()
    assert len(listed) == 2
    kinds = {r.kind for r in listed}
    assert kinds == {ImageAssetKind.UPLOADED, ImageAssetKind.GENERATED}


async def test_list_usable_excludes_expired_by_default(
    catalog: ImageCatalogController, uploaded_store: ImagesDB, generated_store: GeneratedImagesDB
) -> None:
    await uploaded_store.upsert(_uploaded(1))  # fresh
    await uploaded_store.upsert(_uploaded(2, age_hours=48))  # expirado (>24h)
    await generated_store.upsert(_generated(1))  # fresh
    await generated_store.upsert(_generated(2, age_days=20))  # expirado (>14d)
    listed = await catalog.list_usable_assets()
    ids = {r.id for r in listed}
    assert ids == {"up_1", "gen_1"}


async def test_list_usable_include_expired_opts_in(
    catalog: ImageCatalogController, uploaded_store: ImagesDB
) -> None:
    await uploaded_store.upsert(_uploaded(1, age_hours=48))
    listed = await catalog.list_usable_assets(include_expired=True)
    assert len(listed) == 1


async def test_resolve_uploaded_ok(
    catalog: ImageCatalogController, uploaded_store: ImagesDB
) -> None:
    await uploaded_store.upsert(_uploaded(1))
    ref = await catalog.resolve_asset(ImageAssetKind.UPLOADED, "up_1")
    assert ref.kind == ImageAssetKind.UPLOADED
    assert ref.id == "up_1"
    assert ref.kie_url.endswith("up1.png")


async def test_resolve_generated_ok(
    catalog: ImageCatalogController, generated_store: GeneratedImagesDB
) -> None:
    await generated_store.upsert(_generated(1))
    ref = await catalog.resolve_asset(ImageAssetKind.GENERATED, "gen_1")
    assert ref.kind == ImageAssetKind.GENERATED


async def test_resolve_uploaded_missing_raises_typed(catalog: ImageCatalogController) -> None:
    with pytest.raises(ImageNotFoundError):
        await catalog.resolve_asset(ImageAssetKind.UPLOADED, "nope")


async def test_resolve_generated_missing_raises_typed(catalog: ImageCatalogController) -> None:
    with pytest.raises(GeneratedImageNotFoundError):
        await catalog.resolve_asset(ImageAssetKind.GENERATED, "nope")


async def test_resolve_uploaded_expired_raises_typed(
    catalog: ImageCatalogController, uploaded_store: ImagesDB
) -> None:
    await uploaded_store.upsert(_uploaded(1, age_hours=48))
    with pytest.raises(ImageExpiredError):
        await catalog.resolve_asset(ImageAssetKind.UPLOADED, "up_1")


async def test_resolve_generated_expired_raises_typed(
    catalog: ImageCatalogController, generated_store: GeneratedImagesDB
) -> None:
    await generated_store.upsert(_generated(1, age_days=20))
    with pytest.raises(GeneratedImageExpiredError):
        await catalog.resolve_asset(ImageAssetKind.GENERATED, "gen_1")


async def test_colliding_ids_distinguished_by_kind(
    catalog: ImageCatalogController, uploaded_store: ImagesDB, generated_store: GeneratedImagesDB
) -> None:
    """Si por casualidad un uploaded y un generated comparten id, el kind
    discrimina (no colisionan en el resolver porque van a stores distintos)."""
    same_id = "x"
    await uploaded_store.upsert(
        UploadedImage(
            id=same_id,
            label="upload",
            local_path="/tmp/u.png",
            kie_url="https://a.example/u.png",
            kie_file_path="u.png",
            file_size=100,
            mime_type="image/png",
        )
    )
    await generated_store.upsert(
        GeneratedImage(
            id=same_id,
            label="generated",
            prompt="x",
            kie_url="https://a.example/g.png",
            kie_file_path="g.png",
        )
    )
    up_ref = await catalog.resolve_asset(ImageAssetKind.UPLOADED, same_id)
    gen_ref = await catalog.resolve_asset(ImageAssetKind.GENERATED, same_id)
    assert up_ref.label == "upload"
    assert gen_ref.label == "generated"
    assert up_ref.kie_url != gen_ref.kie_url
