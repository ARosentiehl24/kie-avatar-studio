from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from kie_avatar_studio.app import KieAvatarStudioApp
from kie_avatar_studio.config import Settings
from kie_avatar_studio.domain.events import WorkflowJobUpdated
from kie_avatar_studio.domain.models import (
    ModelCreation,
    ModelCreationMethod,
    StepType,
    WorkflowJob,
    WorkflowPreSettings,
    WorkflowStatus,
    WorkflowStep,
    WorkflowStepStatus,
)
from kie_avatar_studio.ui.screens.workflow_detail import WorkflowDetailScreen


class _FakeWorkflowController:
    def __init__(self, workflow: WorkflowJob) -> None:
        self.workflow = workflow
        self.recreate_calls: list[tuple[str, int]] = []

    async def get_workflow(self, workflow_id: str) -> WorkflowJob | None:
        if workflow_id != self.workflow.id:
            return None
        return self.workflow

    def subscribe(self, _callback: Callable[[WorkflowJobUpdated], None]) -> Callable[[], None]:
        return lambda: None

    async def recreate_step(self, workflow_id: str, step_number: int) -> WorkflowJob:
        self.recreate_calls.append((workflow_id, step_number))
        self.workflow.status = WorkflowStatus.QUEUED
        self.workflow.steps[0].status = WorkflowStepStatus.QUEUED
        return self.workflow


def _build_app(tmp_path: Path) -> KieAvatarStudioApp:
    settings = Settings(
        kie_api_key="test-key",
        data_dir=tmp_path / "data",
        outputs_dir=tmp_path / "outputs",
        inputs_dir=tmp_path / "inputs",
        presets_dir=tmp_path / "presets",
        logs_dir=tmp_path / "logs",
    )
    settings.ensure_dirs()
    app = KieAvatarStudioApp(settings=settings)

    async def fake_check() -> float | None:
        return None

    app._check_credits = fake_check  # type: ignore[method-assign]
    return app


def _workflow(tmp_path: Path) -> WorkflowJob:
    return WorkflowJob(
        id="wf_detail",
        name="Detalle",
        slug="detalle",
        source_json_path="workflows/detail.json",
        output_dir=str(tmp_path / "outputs" / "wf_detail"),
        pre_settings=WorkflowPreSettings(
            model_creation=ModelCreation(
                method=ModelCreationMethod.PROMPT,
                prompt="modelo base",
            )
        ),
        status=WorkflowStatus.COMPLETED,
        steps=[
            WorkflowStep(
                step=1,
                scene_name="Hook",
                scene_slug="hook",
                type=StepType.A_ROLL,
                prompt="Persona hablando a cámara",
                text="Hola",
                status=WorkflowStepStatus.COMPLETED,
                video_task_id="veo_1",
                video_path=str(tmp_path / "outputs" / "wf_detail" / "step_01_hook" / "video.mp4"),
            )
        ],
    )


async def test_workflow_detail_recreate_button_uses_selected_step(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    workflow = _workflow(tmp_path)
    controller = _FakeWorkflowController(workflow)

    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        app.push_screen(
            WorkflowDetailScreen(
                controller=controller,  # type: ignore[arg-type]
                workflow_id=workflow.id,
            )
        )
        await pilot.pause()
        await pilot.click("#workflow-detail-recreate-step")
        await pilot.pause()

    assert controller.recreate_calls == [(workflow.id, 1)]
