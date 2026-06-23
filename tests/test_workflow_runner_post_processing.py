from __future__ import annotations

import asyncio
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

    async def download_base_locally(
        self, _ref: ImageAssetRef, output_dir: Path, workflow_slug: str | None = None
    ) -> None:
        filename = f"{workflow_slug}_base.png" if workflow_slug else "base.png"
        (output_dir / filename).write_bytes(b"base")


class _StepRunner:
    async def run(self, step: WorkflowStep, _context: object, on_transition) -> None:
        step.status = WorkflowStepStatus.COMPLETED
        step.completed_at = datetime.now(UTC)
        await on_transition(step)


class _ElevenLabs:
    async def speech_to_speech_to_file(self, *_args: object, **_kwargs: object) -> Path:
        raise AssertionError("apply_voice_changer está parcheado en este test")


class _FFmpegProbe:
    pass


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
    ffmpeg_probe = _FFmpegProbe()

    async def _fake_concat(
        _steps: list[WorkflowStep], output_dir: Path, *, ffmpeg: object, workflow_slug: str
    ) -> Path:
        assert ffmpeg is ffmpeg_probe
        final_video = output_dir / f"{workflow_slug}_final.mp4"
        final_video.write_bytes(b"video")
        (output_dir / f"{workflow_slug}_final_audio.mp3").write_bytes(b"audio")
        return final_video

    concat.side_effect = _fake_concat

    async def _fake_voice(
        _audio: Path, output: Path, _cfg: VoiceChangerSettings, _client: object
    ) -> Path:
        output.write_bytes(b"changed")
        return output

    voice.side_effect = _fake_voice
    runner = WorkflowRunner(
        tmp_settings,
        deps=WorkflowRunnerDeps(
            repository=repo,  # type: ignore[arg-type]
            manifest_writer=manifest,  # type: ignore[arg-type]
            step_runner=_StepRunner(),  # type: ignore[arg-type]
            base_resolver=_BaseResolver(),  # type: ignore[arg-type]
        ),
        elevenlabs_client=_ElevenLabs(),
        ffmpeg=ffmpeg_probe,  # type: ignore[arg-type]
    )

    with (
        patch("kie_avatar_studio.app_layer.workflow_runner.concatenate_workflow_videos", concat),
        patch("kie_avatar_studio.app_layer.workflow_runner.apply_voice_changer", voice),
    ):
        result = await runner.run(
            _workflow(tmp_path / "outputs" / "wf_post", with_voice_changer=True)
        )

    assert result.status == WorkflowStatus.COMPLETED
    concat.assert_awaited_once()
    voice.assert_awaited_once()
    assert (tmp_path / "outputs" / "wf_post" / "post_final_audio.mp3").is_file()


async def test_workflow_runner_skips_voice_changer_when_not_configured(
    tmp_settings,
    tmp_path: Path,
) -> None:
    repo = _Repo()
    manifest = _ManifestWriter()
    concat = AsyncMock()
    voice = AsyncMock()

    async def _fake_concat(
        _steps: list[WorkflowStep], output_dir: Path, *, ffmpeg: object, workflow_slug: str
    ) -> Path:
        final_video = output_dir / f"{workflow_slug}_final.mp4"
        final_video.write_bytes(b"video")
        (output_dir / f"{workflow_slug}_final_audio.mp3").write_bytes(b"audio")
        return final_video

    concat.side_effect = _fake_concat
    runner = WorkflowRunner(
        tmp_settings,
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
        result = await runner.run(
            _workflow(tmp_path / "outputs" / "wf_post", with_voice_changer=False)
        )

    assert result.status == WorkflowStatus.COMPLETED
    concat.assert_awaited_once()
    voice.assert_not_awaited()


async def test_workflow_runner_keeps_partial_status_when_postprocess_fails(
    tmp_settings,
    tmp_path: Path,
) -> None:
    repo = _Repo()
    manifest = _ManifestWriter()
    concat = AsyncMock(side_effect=RuntimeError("ffmpeg boom"))

    class _PartialStepRunner:
        async def run(self, step: WorkflowStep, _context: object, on_transition) -> None:
            if step.step == 1:
                step.status = WorkflowStepStatus.COMPLETED
            else:
                step.status = WorkflowStepStatus.FAILED
                step.error = "veo down"
            step.completed_at = datetime.now(UTC)
            await on_transition(step)

    workflow = WorkflowJob(
        id="wf_partial_post",
        name="Partial",
        slug="partial",
        source_json_path="workflows/partial.json",
        output_dir=str(tmp_path / "outputs" / "wf_partial_post"),
        pre_settings=WorkflowPreSettings(
            model_creation=ModelCreation(
                method=ModelCreationMethod.PROMPT,
                prompt="modelo base",
            )
        ),
        steps=[
            WorkflowStep(
                step=1,
                scene_name="Hook",
                scene_slug="hook",
                type=StepType.A_ROLL,
                prompt="Persona a cámara",
                text="Hola",
            ),
            WorkflowStep(
                step=2,
                scene_name="B-roll",
                scene_slug="b_roll",
                type=StepType.B_ROLL,
                prompt="Producto",
            ),
        ],
    )
    runner = WorkflowRunner(
        tmp_settings,
        deps=WorkflowRunnerDeps(
            repository=repo,  # type: ignore[arg-type]
            manifest_writer=manifest,  # type: ignore[arg-type]
            step_runner=_PartialStepRunner(),  # type: ignore[arg-type]
            base_resolver=_BaseResolver(),  # type: ignore[arg-type]
        ),
    )

    with patch("kie_avatar_studio.app_layer.workflow_runner.concatenate_workflow_videos", concat):
        result = await runner.run(workflow)

    assert result.status == WorkflowStatus.PARTIALLY_FAILED
    assert result.error == "postproceso falló; revisar logs"
    concat.assert_awaited_once()


async def test_workflow_runner_runs_in_series_when_step_sets_new_base(
    tmp_settings,
    tmp_path: Path,
) -> None:
    repo = _Repo()
    manifest = _ManifestWriter()
    concat = AsyncMock()

    async def _fake_concat(
        _steps: list[WorkflowStep], output_dir: Path, *, ffmpeg: object, workflow_slug: str
    ) -> Path:
        final_video = output_dir / f"{workflow_slug}_final.mp4"
        final_video.write_bytes(b"video")
        (output_dir / f"{workflow_slug}_final_audio.mp3").write_bytes(b"audio")
        return final_video

    concat.side_effect = _fake_concat

    class _StepRunnerProbe:
        def __init__(self) -> None:
            self.active = 0
            self.max_active = 0
            self.order: list[int] = []

        async def run(self, step: WorkflowStep, _context: object, on_transition) -> None:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.order.append(step.step)
            await asyncio.sleep(0.01)
            step.status = WorkflowStepStatus.COMPLETED
            step.completed_at = datetime.now(UTC)
            await on_transition(step)
            self.active -= 1

    step_probe = _StepRunnerProbe()
    runner = WorkflowRunner(
        tmp_settings,
        deps=WorkflowRunnerDeps(
            repository=repo,  # type: ignore[arg-type]
            manifest_writer=manifest,  # type: ignore[arg-type]
            step_runner=step_probe,  # type: ignore[arg-type]
            base_resolver=_BaseResolver(),  # type: ignore[arg-type]
        ),
    )

    workflow = _workflow(tmp_path / "outputs" / "wf_series", with_voice_changer=False)
    workflow.steps = [
        WorkflowStep(
            step=1,
            scene_name="Cambio de locación",
            scene_slug="cambio_locacion",
            type=StepType.B_ROLL,
            change_scene=True,
            scene_description="Cocina luminosa",
            prompt="Plano del talento en cocina",
            text="",
            set_as_base=True,
        ),
        WorkflowStep(
            step=2,
            scene_name="Plano de continuidad",
            scene_slug="plano_continuidad",
            type=StepType.A_ROLL,
            change_scene=False,
            prompt="Modelo hablando en la misma locación",
            text="Seguimos en el mismo entorno.",
        ),
    ]

    with patch("kie_avatar_studio.app_layer.workflow_runner.concatenate_workflow_videos", concat):
        result = await runner.run(workflow)

    assert result.status == WorkflowStatus.COMPLETED
    assert step_probe.order == [1, 2]
    assert step_probe.max_active == 1


async def test_workflow_runner_reuses_promoted_base_on_reentry(
    tmp_settings, tmp_path: Path
) -> None:
    """Si un step previo dejó `set_as_base=true`, el reentry debe usar esa base."""

    class _BaseResolverPromoted(_BaseResolver):
        def __init__(self) -> None:
            self.uploaded_paths: list[Path] = []

        async def upload_local_standalone(self, path: Path) -> ImageAssetRef:
            self.uploaded_paths.append(path)
            return ImageAssetRef(
                kind=ImageAssetKind.UPLOADED,
                id="promoted_base",
                label=path.name,
                kie_url="https://tempfile.kie.ai/promoted_base.png",
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )

        async def download_base_locally(
            self,
            ref: ImageAssetRef,
            output_dir: Path,
            workflow_slug: str | None = None,
        ) -> None:
            filename = f"{workflow_slug}_base.png" if workflow_slug else "base.png"
            (output_dir / filename).write_text(ref.id, encoding="utf-8")

    repo = _Repo()
    manifest = _ManifestWriter()
    concat = AsyncMock()

    async def _fake_concat(
        _steps: list[WorkflowStep], output_dir: Path, *, ffmpeg: object, workflow_slug: str
    ) -> Path:
        final_video = output_dir / f"{workflow_slug}_final.mp4"
        final_video.write_bytes(b"video")
        (output_dir / f"{workflow_slug}_final_audio.mp3").write_bytes(b"audio")
        return final_video

    concat.side_effect = _fake_concat
    base_resolver = _BaseResolverPromoted()
    runner = WorkflowRunner(
        tmp_settings,
        deps=WorkflowRunnerDeps(
            repository=repo,  # type: ignore[arg-type]
            manifest_writer=manifest,  # type: ignore[arg-type]
            step_runner=_StepRunner(),  # type: ignore[arg-type]
            base_resolver=base_resolver,  # type: ignore[arg-type]
        ),
    )

    workflow = _workflow(tmp_path / "outputs" / "wf_reentry", with_voice_changer=False)
    promoted_scene = Path(workflow.output_dir) / "step_01_cocina" / "scene.png"
    promoted_scene.parent.mkdir(parents=True, exist_ok=True)
    promoted_scene.write_bytes(b"scene")
    workflow.steps = [
        WorkflowStep(
            step=1,
            scene_name="Cocina",
            scene_slug="cocina",
            type=StepType.B_ROLL,
            change_scene=True,
            scene_description="Cocina luminosa",
            prompt="Plano general cocina",
            text="",
            set_as_base=True,
            scene_image_path=str(promoted_scene),
            status=WorkflowStepStatus.COMPLETED,
            completed_at=datetime.now(UTC),
        ),
        WorkflowStep(
            step=2,
            scene_name="Continuidad",
            scene_slug="continuidad",
            type=StepType.A_ROLL,
            change_scene=False,
            prompt="Modelo en misma locación",
            text="Seguimos en la misma cocina.",
        ),
    ]

    with patch("kie_avatar_studio.app_layer.workflow_runner.concatenate_workflow_videos", concat):
        result = await runner.run(workflow)

    assert result.status == WorkflowStatus.COMPLETED
    assert base_resolver.uploaded_paths == [promoted_scene]
    assert (Path(workflow.output_dir) / "post_base.png").read_text(
        encoding="utf-8"
    ) == "promoted_base"
