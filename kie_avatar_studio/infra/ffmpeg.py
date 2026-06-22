"""Utilidades async para invocar FFmpeg sin bloquear el event loop."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from loguru import logger

from ..domain.errors import FFmpegError

_TMP_SUFFIX_BYTES: Final[int] = 4
_CONCAT_LIST_SUFFIX: Final[str] = ".concat_list"
_MP3_FORMAT: Final[str] = "mp3"
_AAC_FORMAT: Final[str] = "aac"


async def concat_videos(
    video_paths: Sequence[Path],
    output_path: Path,
    *,
    ffmpeg_path: str = "ffmpeg",
) -> Path:
    """Concatena videos MP4 con el concat demuxer de FFmpeg sin re-encode."""
    if not video_paths:
        raise FFmpegError("video_paths no puede estar vacío")

    await asyncio.to_thread(output_path.parent.mkdir, parents=True, exist_ok=True)
    concat_list_path = _build_concat_list_path(output_path)
    payload = "".join(_format_concat_line(path) for path in video_paths)

    try:
        await asyncio.to_thread(concat_list_path.write_text, payload, "utf-8")
        await _run_ffmpeg(
            [
                ffmpeg_path,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_list_path),
                "-c",
                "copy",
                str(output_path),
            ],
            accion="concatenar videos",
        )
        return output_path
    finally:
        await _best_effort_unlink(concat_list_path)


async def extract_audio(
    video_path: Path,
    output_path: Path,
    *,
    ffmpeg_path: str = "ffmpeg",
    audio_format: str = _MP3_FORMAT,
) -> Path:
    """Extrae el audio de un video a MP3 o AAC según `audio_format`."""
    await asyncio.to_thread(output_path.parent.mkdir, parents=True, exist_ok=True)

    normalized_format = audio_format.lower()
    if normalized_format == _MP3_FORMAT:
        codec_args = ["-vn", "-acodec", "libmp3lame", "-q:a", "2"]
    elif normalized_format == _AAC_FORMAT:
        codec_args = ["-vn", "-acodec", "copy"]
    else:
        raise FFmpegError(f"audio_format no soportado: {audio_format!r}")

    await _run_ffmpeg(
        [
            ffmpeg_path,
            "-y",
            "-i",
            str(video_path),
            *codec_args,
            str(output_path),
        ],
        accion=f"extraer audio ({normalized_format})",
    )
    return output_path


async def check_ffmpeg(*, ffmpeg_path: str = "ffmpeg") -> bool:
    """Verifica si el binario de FFmpeg está disponible en PATH."""
    try:
        proc = await asyncio.create_subprocess_exec(
            ffmpeg_path,
            "-version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        logger.debug("FFmpeg no disponible en '{}': {}", ffmpeg_path, exc)
        return False

    _stdout, stderr = await proc.communicate()
    stderr_text = _decode_output(stderr)
    if proc.returncode != 0:
        if stderr_text:
            logger.debug(
                "FFmpeg -version devolvió code={} stderr={}",
                proc.returncode,
                stderr_text,
            )
        return False
    if stderr_text:
        logger.debug("FFmpeg -version stderr: {}", stderr_text)
    return True


class FFmpegCli:
    """Implementación CLI del puerto `FFmpegGateway`."""

    def __init__(self, *, ffmpeg_path: str = "ffmpeg") -> None:
        self._ffmpeg_path = ffmpeg_path

    async def concat_videos(self, video_paths: list[Path], output_path: Path) -> Path:
        return await concat_videos(video_paths, output_path, ffmpeg_path=self._ffmpeg_path)

    async def extract_audio(self, video_path: Path, output_path: Path) -> Path:
        return await extract_audio(video_path, output_path, ffmpeg_path=self._ffmpeg_path)


async def _run_ffmpeg(args: Sequence[str], *, accion: str) -> None:
    """Ejecuta FFmpeg y traduce fallos del subprocess a `FFmpegError`."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        logger.error("No se pudo ejecutar FFmpeg para {}: {}", accion, exc)
        raise FFmpegError(f"no se pudo ejecutar FFmpeg para {accion}: {exc}") from exc

    _stdout, stderr = await proc.communicate()
    stderr_text = _decode_output(stderr)
    if proc.returncode != 0:
        logger.error(
            "FFmpeg falló al {} (code={}): {}",
            accion,
            proc.returncode,
            stderr_text or "<sin stderr>",
        )
        raise FFmpegError(
            f"FFmpeg falló al {accion} (code={proc.returncode}): {stderr_text or '<sin stderr>'}"
        )

    if stderr_text:
        logger.debug("FFmpeg {} stderr: {}", accion, stderr_text)


def _build_concat_list_path(output_path: Path) -> Path:
    """Devuelve un path temporal único para el archivo de concat en `output_path.parent`."""
    token = secrets.token_hex(_TMP_SUFFIX_BYTES)
    return output_path.parent / f"{output_path.name}{_CONCAT_LIST_SUFFIX}.{token}.txt"


def _format_concat_line(video_path: Path) -> str:
    """Serializa una entrada del concat demuxer usando path absoluto y escape seguro."""
    absolute_path = video_path.resolve(strict=False)
    escaped_path = str(absolute_path).replace("'", "'\\''")
    return f"file '{escaped_path}'\n"


def _decode_output(raw: bytes) -> str:
    """Decodifica bytes de stdout/stderr sin fallar ante caracteres inválidos."""
    return raw.decode("utf-8", errors="replace").strip()


async def _best_effort_unlink(path: Path) -> None:
    """Borra `path` si existe, ignorando fallos de cleanup."""
    try:
        await asyncio.to_thread(path.unlink, missing_ok=True)
    except OSError:
        logger.opt(exception=True).debug("No se pudo borrar el tmp de FFmpeg '{}'", path)
