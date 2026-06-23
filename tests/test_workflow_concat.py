from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

from kie_avatar_studio.app_layer.workflow_concat import concatenate_workflow_videos
from kie_avatar_studio.domain.models import StepType, WorkflowStep


class _FakeFFmpeg:
    def __init__(self) -> None:
        self.concat_videos = AsyncMock()
        self.extract_audio = AsyncMock()


def _step(*, step: int, slug: str, attached: bool = True) -> WorkflowStep:
    return WorkflowStep(
        step=step,
        scene_name=f"Escena {step}",
        scene_slug=slug,
        type=StepType.B_ROLL,
        prompt="prompt",
        attached=attached,
    )


def _step_video_path(root: Path, *, step: int, slug: str) -> Path:
    return root / f"step_{step:02d}_{slug}" / f"step_{step:02d}_{slug}_video.mp4"


async def test_concatenate_workflow_videos_returns_none_when_no_attached_videos(
    tmp_path: Path,
) -> None:
    result = await concatenate_workflow_videos(
        [_step(step=1, slug="scene_a")],
        tmp_path,
        ffmpeg=_FakeFFmpeg(),
    )
    assert result is None


async def test_concatenate_workflow_videos_copies_single_video_and_extracts_audio(
    tmp_path: Path,
) -> None:
    video_path = _step_video_path(tmp_path, step=1, slug="scene_a")
    video_path.parent.mkdir(parents=True)
    video_path.write_bytes(b"video")

    async def _fake_extract(_video: Path, out: Path) -> Path:
        out.write_bytes(b"audio")
        return out

    ffmpeg = _FakeFFmpeg()
    ffmpeg.extract_audio.side_effect = _fake_extract
    result = await concatenate_workflow_videos(
        [_step(step=1, slug="scene_a")],
        tmp_path,
        ffmpeg=ffmpeg,
    )

    assert result == tmp_path / "workflow_final.mp4"
    assert (tmp_path / "workflow_final.mp4").read_bytes() == b"video"
    assert (tmp_path / "workflow_final_audio.mp3").read_bytes() == b"audio"
    ffmpeg.extract_audio.assert_awaited_once_with(
        tmp_path / "workflow_final.mp4", tmp_path / "workflow_final_audio.mp3"
    )


async def test_concatenate_workflow_videos_delegates_concat_for_multiple_inputs(
    tmp_path: Path,
) -> None:
    first = _step_video_path(tmp_path, step=1, slug="scene_a")
    second = _step_video_path(tmp_path, step=2, slug="scene_b")
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_bytes(b"a")
    second.write_bytes(b"b")

    async def _fake_concat(paths: tuple[Path, ...] | list[Path], output: Path) -> Path:
        assert paths == [first, second]
        output.write_bytes(b"concat")
        return output

    async def _fake_extract(_video: Path, out: Path) -> Path:
        out.write_bytes(b"audio")
        return out

    ffmpeg = _FakeFFmpeg()
    ffmpeg.concat_videos.side_effect = _fake_concat
    ffmpeg.extract_audio.side_effect = _fake_extract
    result = await concatenate_workflow_videos(
        [
            _step(step=1, slug="scene_a"),
            _step(step=2, slug="scene_b"),
            _step(step=3, slug="scene_c", attached=False),
        ],
        tmp_path,
        ffmpeg=ffmpeg,
    )

    assert result == tmp_path / "workflow_final.mp4"
    ffmpeg.concat_videos.assert_awaited_once()
    ffmpeg.extract_audio.assert_awaited_once_with(
        tmp_path / "workflow_final.mp4", tmp_path / "workflow_final_audio.mp3"
    )


async def test_concatenate_workflow_videos_filters_out_unattached_steps(
    tmp_path: Path,
) -> None:
    attached_video = _step_video_path(tmp_path, step=1, slug="scene_a")
    skipped_video = _step_video_path(tmp_path, step=2, slug="scene_b")
    attached_video.parent.mkdir(parents=True)
    skipped_video.parent.mkdir(parents=True)
    attached_video.write_bytes(b"attached-video")
    skipped_video.write_bytes(b"skipped-video")

    async def _fake_extract(_video: Path, out: Path) -> Path:
        out.write_bytes(b"audio")
        return out

    ffmpeg = _FakeFFmpeg()
    ffmpeg.extract_audio.side_effect = _fake_extract
    result = await concatenate_workflow_videos(
        [
            _step(step=1, slug="scene_a", attached=True),
            _step(step=2, slug="scene_b", attached=False),
        ],
        tmp_path,
        ffmpeg=ffmpeg,
    )

    assert result == tmp_path / "workflow_final.mp4"
    assert (tmp_path / "workflow_final.mp4").read_bytes() == b"attached-video"
    ffmpeg.concat_videos.assert_not_awaited()
    ffmpeg.extract_audio.assert_awaited_once_with(
        tmp_path / "workflow_final.mp4", tmp_path / "workflow_final_audio.mp3"
    )


async def test_concatenate_workflow_videos_accepts_legacy_scene_slug_layout(
    tmp_path: Path,
) -> None:
    legacy_video = tmp_path / "scene_a" / "video.mp4"
    legacy_video.parent.mkdir(parents=True)
    legacy_video.write_bytes(b"legacy-video")

    async def _fake_extract(_video: Path, out: Path) -> Path:
        out.write_bytes(b"audio")
        return out

    ffmpeg = _FakeFFmpeg()
    ffmpeg.extract_audio.side_effect = _fake_extract
    result = await concatenate_workflow_videos(
        [_step(step=1, slug="scene_a")],
        tmp_path,
        ffmpeg=ffmpeg,
    )

    assert result == tmp_path / "workflow_final.mp4"
    assert (tmp_path / "workflow_final.mp4").read_bytes() == b"legacy-video"
