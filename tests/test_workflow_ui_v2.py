"""Regresiones de UI para outputs y labels del workflow v2.0.0."""

from __future__ import annotations

from pathlib import Path

from kie_avatar_studio.domain.models import (
    ModelCreation,
    ModelCreationMethod,
    SceneApprovalMode,
    StepType,
    VeoSettings,
    VoiceChangerSettings,
    WorkflowEntry,
    WorkflowJob,
    WorkflowPreSettings,
    WorkflowStatus,
    WorkflowStep,
    WorkflowStepStatus,
)
from kie_avatar_studio.ui.screens._workflow_format import (
    format_attached_status,
    format_outputs,
    format_workflow_outputs,
    format_workflow_pipeline,
)
from kie_avatar_studio.ui.screens.workflow_summary import WorkflowSummaryScreen


def _pre_settings(*, voice_changer: bool = False) -> WorkflowPreSettings:
    settings = WorkflowPreSettings(
        model_creation=ModelCreation(method=ModelCreationMethod.PROMPT, prompt="Modelo base"),
        scene_approval_mode=SceneApprovalMode.MANUAL,
        veo=VeoSettings(model="veo3_fast", aspect_ratio="9:16", resolution="720p", duration=8),
    )
    if voice_changer:
        settings.voice_changer = VoiceChangerSettings(voice_id="voice-demo")
    return settings


def _step(
    *,
    step: int,
    slug: str,
    attached: bool = True,
    status: WorkflowStepStatus = WorkflowStepStatus.COMPLETED,
) -> WorkflowStep:
    workflow_step = WorkflowStep(
        step=step,
        scene_name=f"Escena {step}",
        scene_slug=slug,
        type=StepType.A_ROLL if step == 1 else StepType.B_ROLL,
        prompt="Prompt",
        attached=attached,
        change_scene=step != 1,
        include_product=step == 2,
        status=status,
    )
    workflow_step.video_path = (
        f"/repo/outputs/step_{step:02d}_{slug}/step_{step:02d}_{slug}_video.mp4"
    )
    return workflow_step


async def _check_credits() -> float | None:
    return None


def test_workflow_formatters_show_v2_outputs_and_attached(tmp_path: Path) -> None:
    output_dir = tmp_path / "wf_ui"
    output_dir.mkdir()
    (output_dir / "final.mp4").write_bytes(b"video")
    (output_dir / "final_audio.mp3").write_bytes(b"audio")
    (output_dir / "voice_changed_audio.mp3").write_bytes(b"voice")
    step = _step(step=1, slug="hook", attached=True)
    workflow = WorkflowJob(
        id="wf-ui",
        name="Workflow UI",
        slug="workflow-ui",
        source_json_path="workflows/ui.json",
        output_dir=str(output_dir),
        pre_settings=_pre_settings(voice_changer=True),
        steps=[step, _step(step=2, slug="detalle", attached=False)],
        status=WorkflowStatus.COMPLETED,
    )

    assert "step_01_hook_video.mp4" in format_outputs(step)
    assert "audio.mp3" not in format_outputs(step)
    assert "✓" in format_attached_status(step)
    assert "✗" in format_attached_status(workflow.steps[1])

    pipeline = format_workflow_pipeline(workflow)
    assert "VEO 3.1" in pipeline
    assert "Voice changer" in pipeline
    assert "listo" in pipeline

    outputs = format_workflow_outputs(workflow)
    assert "workflow-ui_final.mp4" in outputs
    assert "workflow-ui_final_audio.mp3" in outputs
    assert "workflow-ui_voice_changed_audio.mp3" in outputs


def test_workflow_summary_uses_veo_labels_and_hides_legacy_tts_terms() -> None:
    entry = WorkflowEntry(
        name="demo",
        path=Path("workflows/demo.json"),
        workflow_payload={
            "workflow": "Demo",
            "run": [
                {
                    "step": 1,
                    "scene_name": "Hook",
                    "scene_slug": "hook",
                    "type": "a-roll",
                    "change_scene": False,
                    "prompt": "Presenta el producto",
                    "attached": True,
                },
                {
                    "step": 2,
                    "scene_name": "Detalle",
                    "scene_slug": "detalle",
                    "type": "b-roll",
                    "change_scene": True,
                    "include_product": True,
                    "prompt": "Plano recurso",
                    "attached": False,
                },
            ],
        },
    )
    screen = WorkflowSummaryScreen(
        entry=entry,
        pre_settings=_pre_settings(voice_changer=True),
        check_credits=_check_credits,
    )

    settings = screen._render_settings_block()
    steps = screen._render_steps_block()
    ops = screen._render_operations_block()

    assert "VEO 3.1" in settings
    assert "Voice changer" in settings
    assert "Audio language" not in settings
    assert "Voice preset" not in settings
    assert "sin-concat" in steps
    assert "TTS" not in ops
    assert "Avatar Pro" not in ops
    assert "Kling" not in ops
    assert "Postproceso local" in ops
