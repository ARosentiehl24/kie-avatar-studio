"""Tests para los modelos nuevos del subsistema de generación de imágenes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from kie_avatar_studio.domain.models import (
    IMAGE_RESUMABLE_STATUSES,
    IMAGE_TERMINAL_STATUSES,
    GeneratedImage,
    ImageAssetKind,
    ImageAssetRef,
    ImageGenerationSettings,
    ImageJob,
    ImageJobStatus,
)


def test_image_job_default_status() -> None:
    job = ImageJob(id="img_1", label="paisaje", prompt="un atardecer")
    assert job.status is ImageJobStatus.QUEUED
    assert not job.is_terminal()
    # QUEUED es resumable: si la app reinicia con un job recién encolado,
    # se reanuda sin haber pegado a Kie todavía.
    assert job.is_resumable()


def test_image_status_enum_complete() -> None:
    expected = {"queued", "validating", "creating", "polling", "completed", "failed", "cancelled"}
    assert {s.value for s in ImageJobStatus} == expected


def test_terminal_and_resumable_sets_disjoint() -> None:
    assert IMAGE_TERMINAL_STATUSES.isdisjoint(IMAGE_RESUMABLE_STATUSES)
    assert ImageJobStatus.COMPLETED in IMAGE_TERMINAL_STATUSES
    assert ImageJobStatus.POLLING in IMAGE_RESUMABLE_STATUSES


def test_creating_excluded_from_resumables() -> None:
    """CRÍTICO: CREATING no puede ser resumable (riesgo de doble cobro en Kie).

    Si CREATING fuera resumable, un crash entre el POST a Kie y la
    persistencia de `task_id` haría que al reiniciar volvamos a llamar
    `createTask` y paguemos por una segunda generación que ya inició.
    """
    assert ImageJobStatus.CREATING not in IMAGE_RESUMABLE_STATUSES
    assert ImageJobStatus.CREATING not in IMAGE_TERMINAL_STATUSES


def test_image_job_is_terminal_for_each_terminal_status() -> None:
    for status in IMAGE_TERMINAL_STATUSES:
        job = ImageJob(id=f"img_{status.value}", label="x", prompt="x", status=status)
        assert job.is_terminal()
        assert not job.is_resumable()


def test_generated_image_expiration() -> None:
    past = datetime.now(UTC) - timedelta(days=15)
    image = GeneratedImage(
        id="img_old",
        label="old",
        prompt="x",
        kie_url="https://tempfile.redpandaai.co/old.png",
        kie_file_path="img/old.png",
        generated_at=past,
    )
    assert image.is_expired(retention_days=14)
    assert image.time_left(retention_days=14) < timedelta(0)


def test_generated_image_not_expired_within_window() -> None:
    image = GeneratedImage(
        id="img_new",
        label="new",
        prompt="x",
        kie_url="https://tempfile.redpandaai.co/new.png",
        kie_file_path="img/new.png",
    )
    assert not image.is_expired(retention_days=14)
    assert image.time_left(retention_days=14) > timedelta(days=13)


def test_generated_image_optional_metadata() -> None:
    """`file_size` y `mime_type` son opcionales (Nano Banana no siempre los reporta)."""
    image = GeneratedImage(
        id="img_meta_missing",
        label="x",
        prompt="x",
        kie_url="https://tempfile.redpandaai.co/x.png",
        kie_file_path="img/x.png",
    )
    assert image.file_size is None
    assert image.mime_type is None


def test_image_asset_ref_discriminates_kind() -> None:
    uploaded = ImageAssetRef(
        kind=ImageAssetKind.UPLOADED,
        id="up_1",
        label="from upload",
        kie_url="https://tempfile.redpandaai.co/u.png",
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    generated = ImageAssetRef(
        kind=ImageAssetKind.GENERATED,
        id="gen_1",
        label="from gen",
        kie_url="https://tempfile.redpandaai.co/g.png",
        expires_at=datetime.now(UTC) + timedelta(days=14),
    )
    assert uploaded.kind is ImageAssetKind.UPLOADED
    assert generated.kind is ImageAssetKind.GENERATED
    # Ids pueden colisionar sin problema porque el `kind` los distingue.
    same_id_uploaded = ImageAssetRef(
        kind=ImageAssetKind.UPLOADED,
        id="x",
        label="u",
        kie_url="https://a.example/u.png",
        expires_at=datetime.now(UTC),
    )
    same_id_generated = ImageAssetRef(
        kind=ImageAssetKind.GENERATED,
        id="x",
        label="g",
        kie_url="https://a.example/g.png",
        expires_at=datetime.now(UTC),
    )
    assert same_id_uploaded != same_id_generated


def test_image_generation_settings_defaults() -> None:
    settings = ImageGenerationSettings()
    assert settings.aspect_ratio == "auto"
    assert settings.resolution == "1K"
    assert settings.output_format == "jpg"
