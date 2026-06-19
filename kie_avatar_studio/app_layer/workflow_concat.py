from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from importlib import import_module
from pathlib import Path
from shutil import copyfile
from typing import cast

from loguru import logger

from ..domain.models import WorkflowStep

_ConcatVideosFn = Callable[..., Awaitable[Path]]
_ExtractAudioFn = Callable[..., Awaitable[Path]]


def _load_ffmpeg_tools(
    *, ffmpeg_path: str
) -> tuple[_ConcatVideosFn, _ExtractAudioFn]:
    """Carga lazy los helpers de FFmpeg sin acoplar imports de módulo."""
    ffmpeg_module = import_module("kie_avatar_studio.infra.ffmpeg")
    concat_impl = cast(_ConcatVideosFn, ffmpeg_module.concat_videos)
    extract_impl = cast(_ExtractAudioFn, ffmpeg_module.extract_audio)

    async def _concat(video_paths: Sequence[Path], output_path: Path) -> Path:
        return await concat_impl(video_paths, output_path, ffmpeg_path=ffmpeg_path)

    async def _extract(video_path: Path, output_path: Path) -> Path:
        return await extract_impl(video_path, output_path, ffmpeg_path=ffmpeg_path)

    return _concat, _extract


async def concatenate_workflow_videos(
    steps: Sequence[WorkflowStep],
    output_dir: Path,
    *,
    ffmpeg_path: str = "ffmpeg",
) -> Path | None:
    """Concatena los videos attached del workflow y extrae su audio final."""
    videos = [
        output_dir / step.scene_slug / "video.mp4"
        for step in steps
        if step.attached and (output_dir / step.scene_slug / "video.mp4").is_file()
    ]
    if not videos:
        logger.info("Workflow sin videos attached listos para concat en {}", output_dir)
        return None

    final_video_path = output_dir / "final.mp4"
    final_audio_path = output_dir / "final_audio.mp3"
    await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)

    if len(videos) == 1:
        logger.info("Workflow con un solo video attached; copiando a {}", final_video_path)
        await asyncio.to_thread(copyfile, videos[0], final_video_path)
    else:
        logger.info("Concatenando {} videos del workflow en {}", len(videos), final_video_path)
        concat_videos, _ = _load_ffmpeg_tools(ffmpeg_path=ffmpeg_path)
        await concat_videos(videos, final_video_path)

    _, extract_audio = _load_ffmpeg_tools(ffmpeg_path=ffmpeg_path)
    await extract_audio(final_video_path, final_audio_path)
    return final_video_path


__all__ = ["concatenate_workflow_videos"]
