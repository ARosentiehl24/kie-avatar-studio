"""Nombres de artefactos locales generados por workflows."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from .models import WorkflowJob, WorkflowStep

LEGACY_BASE_IMAGE_FILENAME: Final[str] = "base.png"
LEGACY_STEP_SCENE_IMAGE_FILENAME: Final[str] = "scene.png"
LEGACY_STEP_VIDEO_FILENAME: Final[str] = "video.mp4"
LEGACY_FINAL_VIDEO_FILENAME: Final[str] = "final.mp4"
LEGACY_FINAL_AUDIO_FILENAME: Final[str] = "final_audio.mp3"
LEGACY_VOICE_CHANGED_AUDIO_FILENAME: Final[str] = "voice_changed_audio.mp3"


def workflow_base_image_filename(workflow_slug: str) -> str:
    return f"{_safe_slug(workflow_slug)}_base.png"


def step_scene_image_filename(step: WorkflowStep) -> str:
    return f"step_{step.step:02d}_{step.scene_slug}_scene.png"


def step_video_filename(step: WorkflowStep) -> str:
    return f"step_{step.step:02d}_{step.scene_slug}_video.mp4"


def workflow_final_video_filename(workflow_slug: str) -> str:
    return f"{_safe_slug(workflow_slug)}_final.mp4"


def workflow_final_audio_filename(workflow_slug: str) -> str:
    return f"{_safe_slug(workflow_slug)}_final_audio.mp3"


def workflow_voice_changed_audio_filename(workflow_slug: str) -> str:
    return f"{_safe_slug(workflow_slug)}_voice_changed_audio.mp3"


def workflow_final_video_candidates(workflow: WorkflowJob) -> tuple[Path, ...]:
    output_dir = Path(workflow.output_dir)
    return (
        output_dir / workflow_final_video_filename(workflow.slug),
        output_dir / LEGACY_FINAL_VIDEO_FILENAME,
    )


def workflow_final_audio_candidates(workflow: WorkflowJob) -> tuple[Path, ...]:
    output_dir = Path(workflow.output_dir)
    return (
        output_dir / workflow_final_audio_filename(workflow.slug),
        output_dir / LEGACY_FINAL_AUDIO_FILENAME,
    )


def workflow_voice_changed_audio_candidates(workflow: WorkflowJob) -> tuple[Path, ...]:
    output_dir = Path(workflow.output_dir)
    return (
        output_dir / workflow_voice_changed_audio_filename(workflow.slug),
        output_dir / LEGACY_VOICE_CHANGED_AUDIO_FILENAME,
    )


def workflow_base_image_candidates(workflow: WorkflowJob) -> tuple[Path, ...]:
    output_dir = Path(workflow.output_dir)
    return (
        output_dir / workflow_base_image_filename(workflow.slug),
        output_dir / LEGACY_BASE_IMAGE_FILENAME,
    )


def step_dir_name(step: WorkflowStep) -> str:
    return f"step_{step.step:02d}_{step.scene_slug}"


def _safe_slug(slug: str) -> str:
    stripped = slug.strip().strip("_")
    return stripped or "workflow"
