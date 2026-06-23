from __future__ import annotations

from pathlib import Path

from textual.widgets import TextArea

from kie_avatar_studio.app import KieAvatarStudioApp
from kie_avatar_studio.config import Settings
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
from kie_avatar_studio.ui.screens.scene_image_approval import SceneImageApprovalScreen


class _FakeController:
    def __init__(self) -> None:
        self.regenerate_calls: list[dict[str, str | int]] = []

    async def regenerate_scene(
        self,
        workflow_id: str,
        step_number: int,
        *,
        scene_description: str | None = None,
        prompt: str | None = None,
        product_prompt: str | None = None,
        text: str | None = None,
    ) -> WorkflowJob:
        self.regenerate_calls.append(
            {
                "workflow_id": workflow_id,
                "step_number": step_number,
                "scene_description": scene_description or "",
                "prompt": prompt or "",
                "product_prompt": product_prompt or "",
                "text": text or "",
            }
        )
        workflow = _workflow()
        workflow.status = WorkflowStatus.QUEUED
        return workflow

    async def approve_scene(self, _workflow_id: str, _step_number: int) -> WorkflowJob:
        return _workflow()

    async def cancel_step(self, _workflow_id: str, _step_number: int) -> WorkflowJob:
        return _workflow()


def _app(tmp_path: Path) -> KieAvatarStudioApp:
    settings = Settings(
        kie_api_key="test-key",
        data_dir=tmp_path / "data",
        outputs_dir=tmp_path / "outputs",
        inputs_dir=tmp_path / "inputs",
        presets_dir=tmp_path / "presets",
        logs_dir=tmp_path / "logs",
    )
    settings.ensure_dirs()
    return KieAvatarStudioApp(settings=settings)


def _step() -> WorkflowStep:
    return WorkflowStep(
        step=2,
        scene_name="Producto",
        scene_slug="producto",
        type=StepType.B_ROLL,
        change_scene=True,
        scene_description="Mesa vieja",
        prompt="Prompt viejo",
        text="Texto viejo",
        include_product=True,
        product_prompt="Producto viejo",
        status=WorkflowStepStatus.AWAITING_APPROVAL,
    )


def _workflow() -> WorkflowJob:
    return WorkflowJob(
        id="wf-approval",
        name="Approval",
        slug="approval",
        source_json_path="workflows/approval.json",
        output_dir="outputs/wf-approval",
        pre_settings=WorkflowPreSettings(
            model_creation=ModelCreation(method=ModelCreationMethod.PROMPT, prompt="base")
        ),
        steps=[_step()],
        status=WorkflowStatus.AWAITING_APPROVAL,
    )


async def test_regenerate_button_sends_edited_prompts(tmp_path: Path) -> None:
    app = _app(tmp_path)
    controller = _FakeController()
    captured: dict[str, bool | None] = {}

    async with app.run_test(size=(120, 45)) as pilot:
        await pilot.pause()
        app.push_screen(
            SceneImageApprovalScreen(
                controller=controller,  # type: ignore[arg-type]
                workflow=_workflow(),
                step=_step(),
            ),
            lambda result: captured.setdefault("result", result),
        )
        await pilot.pause()
        app.screen.query_one(
            "#scene-approval-scene-description", TextArea
        ).text = "Nueva mesa luminosa"
        app.screen.query_one("#scene-approval-prompt", TextArea).text = "Nuevo prompt VEO"
        app.screen.query_one(
            "#scene-approval-product-prompt", TextArea
        ).text = "Nuevo producto visible"
        app.screen.query_one("#scene-approval-text", TextArea).text = "Nueva voz"
        await app.screen._run_action("regenerate")  # type: ignore[attr-defined]
        await pilot.pause()

    assert captured["result"] is True
    assert controller.regenerate_calls == [
        {
            "workflow_id": "wf-approval",
            "step_number": 2,
            "scene_description": "Nueva mesa luminosa",
            "prompt": "Nuevo prompt VEO",
            "product_prompt": "Nuevo producto visible",
            "text": "Nueva voz",
        }
    ]
