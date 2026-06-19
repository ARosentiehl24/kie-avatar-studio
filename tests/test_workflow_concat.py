from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from kie_avatar_studio.app_layer.workflow_concat import concatenate_workflow_videos
from kie_avatar_studio.domain.models import StepType, WorkflowStep


def _step(*, step: int, slug: str, attached: bool = True) -> WorkflowStep:
    return WorkflowStep(
        step=step,
        scene_name=f"Escena {step}",
        scene_slug=slug,
        type=StepType.B_ROLL,
        prompt="prompt",
        attached=attached,
    )


async def test_concatenate_workflow_videos_returns_none_when_no_attached_videos(
    tmp_path: Path,
) -> None:
    result = await concatenate_workflow_videos([_step(step=1, slug="scene_a")], tmp_path)
    assert result is None


async def test_concatenate_workflow_videos_copies_single_video_and_extracts_audio(
    tmp_path: Path,
) -> None:
    video_path = tmp_path / "scene_a" / "video.mp4"
    video_path.parent.mkdir(parents=True)
    video_path.write_bytes(b"video")

    async def _fake_extract(_video: Path, out: Path) -> Path:
        out.write_bytes(b"audio")
        return out

    extract_audio = AsyncMock(side_effect=_fake_extract)
    with patch(
        "kie_avatar_studio.app_layer.workflow_concat._load_ffmpeg_tools",
        return_value=(AsyncMock(), extract_audio),
    ):
        result = await concatenate_workflow_videos([_step(step=1, slug="scene_a")], tmp_path)

    assert result == tmp_path / "final.mp4"
    assert (tmp_path / "final.mp4").read_bytes() == b"video"
    assert (tmp_path / "final_audio.mp3").read_bytes() == b"audio"
    extract_audio.assert_awaited_once_with(tmp_path / "final.mp4", tmp_path / "final_audio.mp3")


async def test_concatenate_workflow_videos_delegates_concat_for_multiple_inputs(
    tmp_path: Path,
) -> None:
    first = tmp_path / "scene_a" / "video.mp4"
    second = tmp_path / "scene_b" / "video.mp4"
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

    extract_audio = AsyncMock(side_effect=_fake_extract)
    concat_videos = AsyncMock(side_effect=_fake_concat)
    with patch(
        "kie_avatar_studio.app_layer.workflow_concat._load_ffmpeg_tools",
        return_value=(concat_videos, extract_audio),
    ):
        result = await concatenate_workflow_videos(
            [
                _step(step=1, slug="scene_a"),
                _step(step=2, slug="scene_b"),
                _step(step=3, slug="scene_c", attached=False),
            ],
            tmp_path,
            ffmpeg_path="ffmpeg-custom",
        )

    assert result == tmp_path / "final.mp4"
    concat_videos.assert_awaited_once()
    extract_audio.assert_awaited_once_with(tmp_path / "final.mp4", tmp_path / "final_audio.mp3")


async def test_concatenate_workflow_videos_filters_out_unattached_steps(
    tmp_path: Path,
) -> None:
    attached_video = tmp_path / "scene_a" / "video.mp4"
    skipped_video = tmp_path / "scene_b" / "video.mp4"
    attached_video.parent.mkdir(parents=True)
    skipped_video.parent.mkdir(parents=True)
    attached_video.write_bytes(b"attached-video")
    skipped_video.write_bytes(b"skipped-video")

    async def _fake_extract(_video: Path, out: Path) -> Path:
        out.write_bytes(b"audio")
        return out

    concat_videos = AsyncMock()
    extract_audio = AsyncMock(side_effect=_fake_extract)
    with patch(
        "kie_avatar_studio.app_layer.workflow_concat._load_ffmpeg_tools",
        return_value=(concat_videos, extract_audio),
    ):
        result = await concatenate_workflow_videos(
            [
                _step(step=1, slug="scene_a", attached=True),
                _step(step=2, slug="scene_b", attached=False),
            ],
            tmp_path,
        )

    assert result == tmp_path / "final.mp4"
    assert (tmp_path / "final.mp4").read_bytes() == b"attached-video"
    concat_videos.assert_not_awaited()
    extract_audio.assert_awaited_once_with(tmp_path / "final.mp4", tmp_path / "final_audio.mp3")
