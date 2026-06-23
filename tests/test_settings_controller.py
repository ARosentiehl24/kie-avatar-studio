"""Tests para `SettingsController`.

Cubre snapshot + update_endpoints + update_execution + update_concurrency +
update_defaults. Usa un `EnvWriter` fake en memoria para evitar tocar el
filesystem en cada caso.
"""

from __future__ import annotations

import pytest

from kie_avatar_studio.app_layer.settings_controller import (
    EditableSettings,
    SettingsController,
)
from kie_avatar_studio.config import Settings
from kie_avatar_studio.domain.errors import JobValidationError


class _MemoryEnv:
    """`EnvWriter` fake — guarda los pares en memoria, sin tocar disco."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def unset(self, key: str) -> None:
        self.values.pop(key, None)


def _build_controller(**overrides: object) -> tuple[SettingsController, _MemoryEnv]:
    settings = Settings(kie_api_key="", **overrides)  # type: ignore[arg-type]
    env = _MemoryEnv()
    return SettingsController(settings, env), env


def test_snapshot_includes_all_concurrency_fields() -> None:
    controller, _ = _build_controller(
        max_parallel_audio_jobs=4,
        max_parallel_image_jobs=5,
        max_parallel_video_jobs=6,
        max_parallel_upload_jobs=7,
        max_parallel_download_jobs=8,
    )
    snap = controller.snapshot()
    assert isinstance(snap, EditableSettings)
    assert snap.max_parallel_audio_jobs == 4
    assert snap.max_parallel_image_jobs == 5
    assert snap.max_parallel_video_jobs == 6
    assert snap.max_parallel_upload_jobs == 7
    assert snap.max_parallel_download_jobs == 8


def test_snapshot_includes_elevenlabs_api_key() -> None:
    controller, _ = _build_controller(elevenlabs_api_key="sk_test")
    snap = controller.snapshot()
    assert snap.elevenlabs_api_key == "sk_test"


def test_update_concurrency_persists_all_five_keys() -> None:
    controller, env = _build_controller()
    controller.update_concurrency(audio=1, image=2, video=3, upload=4, download=5)
    assert env.values == {
        "MAX_PARALLEL_AUDIO_JOBS": "1",
        "MAX_PARALLEL_IMAGE_JOBS": "2",
        "MAX_PARALLEL_VIDEO_JOBS": "3",
        "MAX_PARALLEL_UPLOAD_JOBS": "4",
        "MAX_PARALLEL_DOWNLOAD_JOBS": "5",
    }


@pytest.mark.parametrize(
    ("kwargs", "expected_field"),
    [
        (
            {"audio": 0, "image": 1, "video": 1, "upload": 1, "download": 1},
            "MAX_PARALLEL_AUDIO_JOBS",
        ),
        (
            {"audio": 1, "image": 0, "video": 1, "upload": 1, "download": 1},
            "MAX_PARALLEL_IMAGE_JOBS",
        ),
        (
            {"audio": 1, "image": 1, "video": 0, "upload": 1, "download": 1},
            "MAX_PARALLEL_VIDEO_JOBS",
        ),
        (
            {"audio": 1, "image": 1, "video": 1, "upload": 0, "download": 1},
            "MAX_PARALLEL_UPLOAD_JOBS",
        ),
        (
            {"audio": 1, "image": 1, "video": 1, "upload": 1, "download": 0},
            "MAX_PARALLEL_DOWNLOAD_JOBS",
        ),
        (
            {"audio": 17, "image": 1, "video": 1, "upload": 1, "download": 1},
            "MAX_PARALLEL_AUDIO_JOBS",
        ),
        (
            {"audio": 1, "image": 1, "video": 1, "upload": 1, "download": 100},
            "MAX_PARALLEL_DOWNLOAD_JOBS",
        ),
    ],
)
def test_update_concurrency_rejects_out_of_range(
    kwargs: dict[str, int], expected_field: str
) -> None:
    controller, env = _build_controller()
    with pytest.raises(JobValidationError, match=expected_field):
        controller.update_concurrency(**kwargs)
    assert env.values == {}, "no debe persistir nada si validación falla"


def test_update_concurrency_accepts_boundary_values() -> None:
    """1 y 16 son los extremos válidos del rango (_MIN_PARALLEL / _MAX_PARALLEL)."""
    controller, env = _build_controller()
    controller.update_concurrency(audio=1, image=16, video=1, upload=16, download=1)
    assert env.values == {
        "MAX_PARALLEL_AUDIO_JOBS": "1",
        "MAX_PARALLEL_IMAGE_JOBS": "16",
        "MAX_PARALLEL_VIDEO_JOBS": "1",
        "MAX_PARALLEL_UPLOAD_JOBS": "16",
        "MAX_PARALLEL_DOWNLOAD_JOBS": "1",
    }
