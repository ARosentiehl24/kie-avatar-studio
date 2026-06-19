from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from kie_avatar_studio.domain.errors import FFmpegError
from kie_avatar_studio.infra import ffmpeg as ffmpeg_module
from kie_avatar_studio.infra.ffmpeg import check_ffmpeg, concat_videos, extract_audio


class _Proc:
    def __init__(self, returncode: int, stderr: bytes = b"") -> None:
        self.returncode = returncode
        self._stderr = stderr

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", self._stderr


async def test_concat_videos_writes_concat_list_and_cleans_up(tmp_path: Path) -> None:
    video_a = tmp_path / "uno con espacios.mp4"
    video_b = tmp_path / "dos.mp4"
    video_a.write_bytes(b"a")
    video_b.write_bytes(b"b")
    output_path = tmp_path / "salida" / "final.mp4"

    captured: dict[str, object] = {}

    async def fake_create(*args: str, **kwargs: object) -> _Proc:
        captured["args"] = args
        captured["kwargs"] = kwargs
        concat_list_path = Path(args[7])
        captured["concat_list_path"] = concat_list_path
        captured["concat_payload"] = concat_list_path.read_text("utf-8")
        return _Proc(0, b"concat ok")

    with patch.object(ffmpeg_module.asyncio, "create_subprocess_exec", new=fake_create):
        result = await concat_videos([video_a, video_b], output_path)

    assert result == output_path
    assert captured["args"] == (
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(captured["concat_list_path"]),
        "-c",
        "copy",
        str(output_path),
    )
    assert captured["kwargs"] == {
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
    }
    expected_payload = (
        f"file '{video_a.resolve(strict=False)}'\n"
        f"file '{video_b.resolve(strict=False)}'\n"
    )
    assert captured["concat_payload"] == expected_payload
    assert output_path.parent.is_dir()
    assert not Path(captured["concat_list_path"]).exists()


@pytest.mark.parametrize(
    ("audio_format", "expected_codec_args"),
    [
        ("mp3", ("-vn", "-acodec", "libmp3lame", "-q:a", "2")),
        ("aac", ("-vn", "-acodec", "copy")),
    ],
)
async def test_extract_audio_uses_expected_codec_args(
    tmp_path: Path,
    audio_format: str,
    expected_codec_args: tuple[str, ...],
) -> None:
    output_path = tmp_path / "audio" / f"track.{audio_format}"
    captured: dict[str, object] = {}

    async def fake_create(*args: str, **kwargs: object) -> _Proc:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _Proc(0)

    with patch.object(ffmpeg_module.asyncio, "create_subprocess_exec", new=fake_create):
        result = await extract_audio(tmp_path / "video.mp4", output_path, audio_format=audio_format)

    assert result == output_path
    assert captured["args"] == (
        "ffmpeg",
        "-y",
        "-i",
        str(tmp_path / "video.mp4"),
        *expected_codec_args,
        str(output_path),
    )
    assert captured["kwargs"] == {
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
    }
    assert output_path.parent.is_dir()


async def test_check_ffmpeg_returns_true_when_version_succeeds() -> None:
    async def fake_create(*_args: str, **_kwargs: object) -> _Proc:
        return _Proc(0, b"ffmpeg version n7")

    with patch.object(ffmpeg_module.asyncio, "create_subprocess_exec", new=fake_create):
        assert await check_ffmpeg() is True


async def test_check_ffmpeg_returns_false_on_non_zero_exit() -> None:
    async def fake_create(*_args: str, **_kwargs: object) -> _Proc:
        return _Proc(1, b"missing codec")

    with patch.object(ffmpeg_module.asyncio, "create_subprocess_exec", new=fake_create):
        assert await check_ffmpeg() is False


async def test_check_ffmpeg_returns_false_when_binary_is_missing() -> None:
    async def fake_create(*_args: str, **_kwargs: object) -> _Proc:
        raise OSError("not found")

    with patch.object(ffmpeg_module.asyncio, "create_subprocess_exec", new=fake_create):
        assert await check_ffmpeg() is False


async def test_extract_audio_raises_ffmpeg_error_on_non_zero_exit(tmp_path: Path) -> None:
    async def fake_create(*_args: str, **_kwargs: object) -> _Proc:
        return _Proc(1, b"decoder failed")

    with (
        patch.object(ffmpeg_module.asyncio, "create_subprocess_exec", new=fake_create),
        pytest.raises(FFmpegError, match="decoder failed"),
    ):
        await extract_audio(tmp_path / "video.mp4", tmp_path / "audio.mp3")
