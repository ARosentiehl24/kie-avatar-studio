from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from shutil import copyfile

from loguru import logger

from ..domain.models import WorkflowStep
from ..domain.ports import FFmpegGateway

_VIDEO_FILENAME = "video.mp4"


async def concatenate_workflow_videos(
    steps: Sequence[WorkflowStep],
    output_dir: Path,
    *,
    ffmpeg: FFmpegGateway,
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

    final_video_path = output_dir / "final.mp4"
    final_audio_path = output_dir / "final_audio.mp3"
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
    `output_dir / step_<NN>_<scene_slug> / video.mp4`

    Layout legacy:
    `output_dir / <scene_slug> / video.mp4`
    """
    canonical = output_dir / f"step_{step.step:02d}_{step.scene_slug}" / _VIDEO_FILENAME
    if await asyncio.to_thread(canonical.is_file):
        return canonical
    legacy = output_dir / step.scene_slug / _VIDEO_FILENAME
    if await asyncio.to_thread(legacy.is_file):
        return legacy
    return None


__all__ = ["concatenate_workflow_videos"]
