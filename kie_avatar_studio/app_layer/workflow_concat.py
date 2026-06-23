from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from shutil import copyfile

from loguru import logger

from ..domain.models import WorkflowStep
from ..domain.ports import FFmpegGateway
from ..domain.workflow_artifacts import (
    LEGACY_STEP_VIDEO_FILENAME,
    step_dir_name,
    step_video_filename,
    workflow_final_audio_filename,
    workflow_final_video_filename,
)

_VIDEO_FILENAME = LEGACY_STEP_VIDEO_FILENAME


async def concatenate_workflow_videos(
    steps: Sequence[WorkflowStep],
    output_dir: Path,
    *,
    ffmpeg: FFmpegGateway,
    workflow_slug: str = "workflow",
) -> Path | None:
    """Concatena los videos attached del workflow y extrae su audio final."""
    videos: list[Path] = []
    attached_steps = 0
    for step in steps:
        if not step.attached:
            continue
        attached_steps += 1
        path = await _resolve_step_video_path(output_dir=output_dir, step=step)
        if path is not None:
            videos.append(path)
    if not videos:
        logger.info(
            "Workflow sin videos attached listos para concat en {} "
            "(attached_steps={}, videos_encontrados=0)",
            output_dir,
            attached_steps,
        )
        return None

    final_video_path = output_dir / workflow_final_video_filename(workflow_slug)
    final_audio_path = output_dir / workflow_final_audio_filename(workflow_slug)
    await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)

    if len(videos) == 1:
        logger.info("Workflow con un solo video attached; copiando a {}", final_video_path)
        await asyncio.to_thread(copyfile, videos[0], final_video_path)
    else:
        logger.info("Concatenando {} videos del workflow en {}", len(videos), final_video_path)
        await ffmpeg.concat_videos(videos, final_video_path)

    await ffmpeg.extract_audio(final_video_path, final_audio_path)
    return final_video_path


async def _resolve_step_video_path(*, output_dir: Path, step: WorkflowStep) -> Path | None:
    """Resuelve el path del video del step con compatibilidad retroactiva.

    Layout actual:
    `output_dir / step_<NN>_<scene_slug> / step_<NN>_<scene_slug>_video.mp4`

    Layout legacy:
    `output_dir / step_<NN>_<scene_slug> / video.mp4`
    `output_dir / <scene_slug> / video.mp4`
    """
    if step.video_path:
        explicit = Path(step.video_path)
        if await asyncio.to_thread(explicit.is_file):
            return explicit
    step_dir = output_dir / step_dir_name(step)
    canonical = step_dir / step_video_filename(step)
    if await asyncio.to_thread(canonical.is_file):
        return canonical
    for legacy in (step_dir / _VIDEO_FILENAME, output_dir / step.scene_slug / _VIDEO_FILENAME):
        if await asyncio.to_thread(legacy.is_file):
            return legacy
    return None


__all__ = ["concatenate_workflow_videos"]
