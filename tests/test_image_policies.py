"""Tests para validators de `domain.policies` agregados para Nano Banana 2."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kie_avatar_studio.domain.errors import ImageGenerationValidationError
from kie_avatar_studio.domain.models import ImageAssetKind, ImageAssetRef, ImageGenerationSettings
from kie_avatar_studio.domain.policies import (
    ASPECT_RATIOS,
    MAX_IMAGE_PROMPT_CHARS,
    MAX_IMAGE_REFS,
    OUTPUT_FORMATS,
    RESOLUTIONS,
    validate_image_prompt,
    validate_image_refs,
    validate_image_settings,
)


def _ref(idx: int, *, kind: ImageAssetKind = ImageAssetKind.UPLOADED) -> ImageAssetRef:
    return ImageAssetRef(
        kind=kind,
        id=f"ref_{idx}",
        label=f"r{idx}",
        kie_url=f"https://tempfile.redpandaai.co/img-{idx}.png",
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )


# --- validate_image_prompt ------------------------------------------------


def test_prompt_ok() -> None:
    validate_image_prompt("un atardecer con palmeras")


def test_prompt_empty_rejected() -> None:
    with pytest.raises(ImageGenerationValidationError):
        validate_image_prompt("")


def test_prompt_whitespace_rejected() -> None:
    with pytest.raises(ImageGenerationValidationError):
        validate_image_prompt("   ")


def test_prompt_too_long_rejected() -> None:
    with pytest.raises(ImageGenerationValidationError):
        validate_image_prompt("a" * (MAX_IMAGE_PROMPT_CHARS + 1))


def test_prompt_at_limit_ok() -> None:
    validate_image_prompt("a" * MAX_IMAGE_PROMPT_CHARS)


# --- validate_image_settings ----------------------------------------------


def test_settings_default_ok() -> None:
    validate_image_settings(ImageGenerationSettings())


def test_settings_all_valid_values() -> None:
    for ratio in ASPECT_RATIOS:
        for res in RESOLUTIONS:
            for fmt in OUTPUT_FORMATS:
                settings = ImageGenerationSettings(
                    aspect_ratio=ratio, resolution=res, output_format=fmt
                )
                validate_image_settings(settings)


def test_settings_bad_aspect_ratio() -> None:
    with pytest.raises(ImageGenerationValidationError, match="aspect_ratio"):
        validate_image_settings(ImageGenerationSettings(aspect_ratio="999:1"))


def test_settings_bad_resolution() -> None:
    with pytest.raises(ImageGenerationValidationError, match="resolution"):
        validate_image_settings(ImageGenerationSettings(resolution="8K"))


def test_settings_bad_output_format() -> None:
    with pytest.raises(ImageGenerationValidationError, match="output_format"):
        validate_image_settings(ImageGenerationSettings(output_format="webp"))


# --- validate_image_refs --------------------------------------------------


def test_refs_empty_ok() -> None:
    validate_image_refs([])


def test_refs_at_limit_ok() -> None:
    validate_image_refs([_ref(i) for i in range(MAX_IMAGE_REFS)])


def test_refs_over_limit_rejected() -> None:
    with pytest.raises(ImageGenerationValidationError, match="máximo"):
        validate_image_refs([_ref(i) for i in range(MAX_IMAGE_REFS + 1)])


def test_refs_duplicate_url_rejected() -> None:
    refs = [_ref(1), _ref(1)]
    with pytest.raises(ImageGenerationValidationError, match="duplicada"):
        validate_image_refs(refs)


def test_refs_malformed_url_rejected() -> None:
    bad = ImageAssetRef(
        kind=ImageAssetKind.UPLOADED,
        id="x",
        label="x",
        kie_url="file:///tmp/x.png",
        expires_at=datetime.now(UTC),
    )
    with pytest.raises(ImageGenerationValidationError, match="URL inválida"):
        validate_image_refs([bad])


def test_refs_mixed_kinds_allowed() -> None:
    """Las refs pueden combinar uploaded + generated en el mismo job."""
    refs = [
        _ref(1, kind=ImageAssetKind.UPLOADED),
        _ref(2, kind=ImageAssetKind.GENERATED),
    ]
    validate_image_refs(refs)
