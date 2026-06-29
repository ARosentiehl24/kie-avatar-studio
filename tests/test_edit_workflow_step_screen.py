from __future__ import annotations

from pathlib import Path

from textual.widgets import Static, TextArea

from kie_avatar_studio.app import KieAvatarStudioApp
from kie_avatar_studio.config import Settings
from kie_avatar_studio.domain.models import StepType, WorkflowStep
from kie_avatar_studio.ui.screens.edit_workflow_step import EditWorkflowStepScreen


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
        step=3,
        scene_name="Original",
        scene_slug="original",
        type=StepType.A_ROLL,
        scene_description="Escena vieja",
        prompt="Prompt viejo",
        text="Texto viejo",
    )


async def test_edit_workflow_step_screen_returns_edited_fields(tmp_path: Path) -> None:
    app = _app(tmp_path)
    captured: dict[str, object] = {}

    async with app.run_test(size=(120, 45)) as pilot:
        await pilot.pause()
        app.push_screen(
            EditWorkflowStepScreen(_step()), lambda result: captured.update(result=result)
        )
        await pilot.pause()
        app.screen.query_one("#edit-step-scene-name", TextArea).text = "Nuevo hook"
        app.screen.query_one("#edit-step-scene-description", TextArea).text = "Escena minimalista"
        app.screen.query_one("#edit-step-prompt", TextArea).text = "Prompt claro"
        app.screen.query_one("#edit-step-text", TextArea).text = "Texto claro"
        app.screen._save()  # type: ignore[attr-defined]
        await pilot.pause()

    result = captured["result"]
    assert result is not None
    assert result.scene_name == "Nuevo hook"
    assert result.scene_description == "Escena minimalista"
    assert result.prompt == "Prompt claro"
    assert result.product_prompt is None
    assert result.text == "Texto claro"


async def test_edit_workflow_step_screen_shows_validation_error(tmp_path: Path) -> None:
    app = _app(tmp_path)
    captured: dict[str, object] = {}

    async with app.run_test(size=(120, 45)) as pilot:
        await pilot.pause()
        app.push_screen(
            EditWorkflowStepScreen(_step()), lambda result: captured.update(result=result)
        )
        await pilot.pause()
        app.screen.query_one("#edit-step-prompt", TextArea).text = ""
        app.screen._save()  # type: ignore[attr-defined]
        await pilot.pause()
        error = str(app.screen.query_one("#edit-step-error", Static).render())

    assert "prompt vacío" in error
    assert "result" not in captured
