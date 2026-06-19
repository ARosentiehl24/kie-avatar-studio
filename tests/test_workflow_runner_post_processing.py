from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from kie_avatar_studio.app_layer.workflow_runner import WorkflowRunner, WorkflowRunnerDeps
from kie_avatar_studio.domain.models import (
    ImageAssetKind,
    ImageAssetRef,
    ModelCreation,
    ModelCreationMethod,
    StepType,
    VoiceChangerSettings,
    WorkflowJob,
    WorkflowPreSettings,
    WorkflowStatus,
    WorkflowStep,
    WorkflowStepStatus,
)


class _Repo:
    def __init__(self) -> None:
        self.headers: list[WorkflowStatus] = []
        self.steps: list[tuple[str, int]] = []

    async def update_workflow_header(self, job: WorkflowJob) -> None:
        self.headers.append(job.status)

    async def upsert_step(self, workflow_id: str, step: WorkflowStep) -> None:
        self.steps.append((workflow_id, step.step))


class _ManifestWriter:
    def __init__(self) -> None:
        self.calls = 0

    async def write(self, _workflow: WorkflowJob) -> bool:
        self.calls += 1
        return True


class _BaseResolver:
    async def resolve_voice(self, _workflow: WorkflowJob) -> tuple[str, None]:
        return "voice", None

    async def resolve_base_image(self, _workflow: WorkflowJob) -> ImageAssetRef:
        return ImageAssetRef(
            kind=ImageAssetKind.GENERATED,
            id="base",
            label="base",
            kie_url="https://tempfile.kie.ai/base.png",
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )

    async def download_base_locally(self, _ref: ImageAssetRef, output_dir: Path) -> None:
        (output_dir / "base.png").write_bytes(b"base")


class _StepRunner:
    async def run(self, step: WorkflowStep, _context: object, on_transition) -> None:
        step.status = WorkflowStepStatus.COMPLETED
        step.completed_at = datetime.now(UTC)
        await on_transition(step)


class _ElevenLabs:
    async def speech_to_speech(self, *_args: object, **_kwargs: object) -> bytes:
        return b"unused"


def _workflow(output_dir: Path, *, with_voice_changer: bool) -> WorkflowJob:
    return WorkflowJob(
        id="wf_post",
        name="Post",
        slug="post",
        source_json_path="workflows/post.json",
        output_dir=str(output_dir),
        pre_settings=WorkflowPreSettings(
            model_creation=ModelCreation(
                method=ModelCreationMethod.PROMPT,
                prompt="modelo base",
            ),
            voice_changer=(
                VoiceChangerSettings(voice_id="voice_123") if with_voice_changer else None
            ),
        ),
        steps=[
            WorkflowStep(
                step=1,
                scene_name="Hook",
                scene_slug="hook",
                type=StepType.A_ROLL,
                prompt="Persona a cámara",
                text="Hola",
            )
        ],
    )


async def test_workflow_runner_runs_concat_and_voice_changer(tmp_settings, tmp_path: Path) -> None:
    repo = _Repo()
    manifest = _ManifestWriter()
    concat = AsyncMock()
    voice = AsyncMock()

    async def _fake_concat(_steps: list[WorkflowStep], output_dir: Path, *, ffmpeg_path: str) -> Path:
        assert ffmpeg_path == "ffmpeg-custom"
        final_video = output_dir / "final.mp4"
        final_video.write_bytes(b"video")
        (output_dir / "final_audio.mp3").write_bytes(b"audio")
        return final_video

    concat.side_effect = _fake_concat

    async def _fake_voice(_audio: Path, output: Path, _cfg: VoiceChangerSettings, _client: object) -> Path:
        output.write_bytes(b"changed")
        return output

    voice.side_effect = _fake_voice
    runner = WorkflowRunner(
        tmp_settings,
        client=None,  # type: ignore[arg-type]
        deps=WorkflowRunnerDeps(
            repository=repo,  # type: ignore[arg-type]
            manifest_writer=manifest,  # type: ignore[arg-type]
            step_runner=_StepRunner(),  # type: ignore[arg-type]
            base_resolver=_BaseResolver(),  # type: ignore[arg-type]
        ),
        elevenlabs_client=_ElevenLabs(),
        ffmpeg_path="ffmpeg-custom",
    )

    with (
        patch("kie_avatar_studio.app_layer.workflow_runner.concatenate_workflow_videos", concat),
        patch("kie_avatar_studio.app_layer.workflow_runner.apply_voice_changer", voice),
    ):
        result = await runner.run(_workflow(tmp_path / "outputs" / "wf_post", with_voice_changer=True))

    assert result.status == WorkflowStatus.COMPLETED
    concat.assert_awaited_once()
    voice.assert_awaited_once()
    assert (tmp_path / "outputs" / "wf_post" / "final_audio.mp3").is_file()


async def test_workflow_runner_skips_voice_changer_when_not_configured(
    tmp_settings,
    tmp_path: Path,
) -> None:
    repo = _Repo()
    manifest = _ManifestWriter()
    concat = AsyncMock()
    voice = AsyncMock()

    async def _fake_concat(_steps: list[WorkflowStep], output_dir: Path, *, ffmpeg_path: str) -> Path:
        final_video = output_dir / "final.mp4"
        final_video.write_bytes(b"video")
        (output_dir / "final_audio.mp3").write_bytes(b"audio")
        return final_video

    concat.side_effect = _fake_concat
    runner = WorkflowRunner(
        tmp_settings,
        client=None,  # type: ignore[arg-type]
        deps=WorkflowRunnerDeps(
            repository=repo,  # type: ignore[arg-type]
            manifest_writer=manifest,  # type: ignore[arg-type]
            step_runner=_StepRunner(),  # type: ignore[arg-type]
            base_resolver=_BaseResolver(),  # type: ignore[arg-type]
        ),
    )

    with (
        patch("kie_avatar_studio.app_layer.workflow_runner.concatenate_workflow_videos", concat),
        patch("kie_avatar_studio.app_layer.workflow_runner.apply_voice_changer", voice),
    ):
        result = await runner.run(_workflow(tmp_path / "outputs" / "wf_post", with_voice_changer=False))

    assert result.status == WorkflowStatus.COMPLETED
    concat.assert_awaited_once()
    voice.assert_not_awaited()
