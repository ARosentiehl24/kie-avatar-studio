from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from kie_avatar_studio.app_layer.workflow_runner import WorkflowRunner, WorkflowRunnerDeps
from kie_avatar_studio.domain.models import (
    ImageAssetKind,
    ImageAssetRef,
    ModelCreation,
    ModelCreationMethod,
    StepType,
    WorkflowJob,
    WorkflowPreSettings,
    WorkflowStatus,
    WorkflowStep,
)


class _Repo:
    def __init__(self) -> None:
        self.saved: list[WorkflowJob] = []

    async def upsert(self, job: WorkflowJob) -> None:
        self.saved.append(job.model_copy(deep=True))

    async def update_workflow_header(self, job: WorkflowJob) -> None:
        self.saved.append(job.model_copy(deep=True))


class _ManifestWriter:
    async def write(self, _workflow: WorkflowJob) -> None:
        raise AssertionError("no debe escribir manifest si output_dir es inválido")


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

    async def download_base_locally(self, _ref: ImageAssetRef, _output_dir: Path) -> None:
        raise AssertionError("no debe descargar base si output_dir es inválido")


class _StepRunner:
    async def run(self, *_args: object) -> WorkflowStep:
        raise AssertionError("no debe ejecutar steps si output_dir es inválido")


def _workflow(output_dir: Path) -> WorkflowJob:
    return WorkflowJob(
        id="wf_bad_path",
        name="Bad path",
        slug="bad_path",
        source_json_path="workflows/bad.json",
        output_dir=str(output_dir),
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
            )
        ],
    )


async def test_workflow_runner_rejects_output_dir_outside_outputs(tmp_settings, tmp_path) -> None:
    repo = _Repo()
    runner = WorkflowRunner(
        tmp_settings,
        deps=WorkflowRunnerDeps(
            repository=repo,  # type: ignore[arg-type]
            manifest_writer=_ManifestWriter(),  # type: ignore[arg-type]
            step_runner=_StepRunner(),  # type: ignore[arg-type]
            base_resolver=_BaseResolver(),  # type: ignore[arg-type]
        ),
    )
    outside = tmp_path / "outside" / "wf"

    result = await runner.run(_workflow(outside))

    assert result.status == WorkflowStatus.FAILED
    assert "output_dir del workflow queda fuera de outputs_dir" in (result.error or "")
    assert not outside.exists()
