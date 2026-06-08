"""Tests de los modelos de dominio del workflow automation."""

from __future__ import annotations

import pytest

from kie_avatar_studio.domain.models import (
    ImageAssetKind,
    ModelCreation,
    ModelCreationMethod,
    StepType,
    WorkflowJob,
    WorkflowPreSettings,
    WorkflowProgressKey,
    WorkflowProgressStatus,
    WorkflowStatus,
    WorkflowStep,
    WorkflowStepStatus,
)


def _make_pre_settings(
    method: ModelCreationMethod = ModelCreationMethod.PROMPT,
) -> WorkflowPreSettings:
    """Helper para crear unos pre_settings mínimos válidos."""
    if method == ModelCreationMethod.PROMPT:
        creation = ModelCreation(method=method, prompt="A photorealistic woman")
    elif method == ModelCreationMethod.LOCAL:
        creation = ModelCreation(method=method, local_path="inputs/modelo.png")
    else:
        creation = ModelCreation(
            method=method,
            asset_kind=ImageAssetKind.GENERATED,
            asset_id="img_20260605_abc123",
        )
    return WorkflowPreSettings(
        audio_language="es-419",
        voice_preset_id="latina_warm",
        model_creation=creation,
    )


def _make_step(
    *,
    step: int = 1,
    type_: StepType = StepType.A_ROLL,
    text: str = "Hola, esto es una prueba.",
    change_scene: bool = False,
) -> WorkflowStep:
    return WorkflowStep(
        step=step,
        scene_name=f"Escena {step}",
        scene_slug=f"escena_{step}",
        type=type_,
        change_scene=change_scene,
        scene_description="",
        prompt="Una mujer hablando a cámara, plano medio.",
        text=text,
    )


def test_voice_preset_alias_accepts_user_facing_json() -> None:
    """El JSON del usuario trae `voice_preset` (sin sufijo _id) y debe parsear OK."""
    payload = {
        "audio_language": "es-419",
        "voice_preset": "latina_warm_authentic",
        "model_creation": {
            "method": "prompt",
            "prompt": "Una persona",
        },
    }
    pre = WorkflowPreSettings.model_validate(payload)
    assert pre.voice_preset_id == "latina_warm_authentic"
    # Y serializa de vuelta con el alias para que el manifest matchee el input.
    dumped = pre.model_dump(by_alias=True)
    assert dumped["voice_preset"] == "latina_warm_authentic"
    assert "voice_preset_id" not in dumped


def test_voice_preset_id_also_accepts_internal_name() -> None:
    """`populate_by_name=True` también acepta el nombre interno (compat tests)."""
    pre = WorkflowPreSettings.model_validate(
        {
            "voice_preset_id": "custom",
            "model_creation": {"method": "catalog", "asset_kind": "generated", "asset_id": "x"},
        }
    )
    assert pre.voice_preset_id == "custom"


def test_workflow_step_progress_uses_typed_enums() -> None:
    """El dict `progress` debe aceptar enums tipados y serializarlos como .value."""
    step = _make_step()
    step.progress[WorkflowProgressKey.SCENE_IMAGE] = WorkflowProgressStatus.COMPLETED
    step.progress[WorkflowProgressKey.AUDIO] = WorkflowProgressStatus.RUNNING
    dumped = step.model_dump(mode="json")
    assert dumped["progress"] == {"scene_image": "completed", "audio": "running"}


def test_workflow_step_is_terminal_only_for_terminal_statuses() -> None:
    step = _make_step()
    assert not step.is_terminal()
    step.status = WorkflowStepStatus.QUEUED
    assert not step.is_terminal()
    step.status = WorkflowStepStatus.PREPARING
    assert not step.is_terminal()
    step.status = WorkflowStepStatus.RENDERING
    assert not step.is_terminal()
    step.status = WorkflowStepStatus.DOWNLOADING
    assert not step.is_terminal()
    step.status = WorkflowStepStatus.COMPLETED
    assert step.is_terminal()
    step.status = WorkflowStepStatus.FAILED
    assert step.is_terminal()
    step.status = WorkflowStepStatus.CANCELLED
    assert step.is_terminal()


def test_workflow_job_is_terminal_and_resumable_disjoint() -> None:
    workflow = WorkflowJob(
        id="wf_test_001",
        name="Test",
        slug="test",
        source_json_path="workflows/test.json",
        output_dir="outputs/wf_test_001",
        pre_settings=_make_pre_settings(),
        steps=[_make_step()],
    )
    # QUEUED: resumable, no terminal.
    assert workflow.is_resumable()
    assert not workflow.is_terminal()
    workflow.status = WorkflowStatus.RUNNING
    assert workflow.is_resumable()
    assert not workflow.is_terminal()
    workflow.status = WorkflowStatus.COMPLETED
    assert not workflow.is_resumable()
    assert workflow.is_terminal()
    workflow.status = WorkflowStatus.PARTIALLY_FAILED
    assert workflow.is_terminal()
    workflow.status = WorkflowStatus.FAILED
    assert workflow.is_terminal()
    workflow.status = WorkflowStatus.CANCELLED
    assert workflow.is_terminal()


def test_workflow_step_by_number_returns_step_or_none() -> None:
    workflow = WorkflowJob(
        id="wf_test_001",
        name="Test",
        slug="test",
        source_json_path="workflows/test.json",
        output_dir="outputs/wf_test_001",
        pre_settings=_make_pre_settings(),
        steps=[_make_step(step=1), _make_step(step=2)],
    )
    assert workflow.step_by_number(1) is not None
    assert workflow.step_by_number(2) is not None
    assert workflow.step_by_number(99) is None


def test_model_creation_serialization_roundtrip_preserves_method() -> None:
    """El JSON debe roundtripper sin perder el discriminador `method`."""
    creation = ModelCreation(method=ModelCreationMethod.LOCAL, local_path="inputs/m.png")
    raw = creation.model_dump_json()
    restored = ModelCreation.model_validate_json(raw)
    assert restored.method == ModelCreationMethod.LOCAL
    assert restored.local_path == "inputs/m.png"


def test_workflow_default_status_is_queued() -> None:
    workflow = WorkflowJob(
        id="wf_test_001",
        name="Test",
        slug="test",
        source_json_path="workflows/test.json",
        output_dir="outputs/wf_test_001",
        pre_settings=_make_pre_settings(),
        steps=[_make_step()],
    )
    assert workflow.status == WorkflowStatus.QUEUED
    assert not workflow.manifest_write_failed


@pytest.mark.parametrize(
    "method",
    [ModelCreationMethod.PROMPT, ModelCreationMethod.LOCAL, ModelCreationMethod.CATALOG],
)
def test_workflow_with_all_method_types_constructs(method: ModelCreationMethod) -> None:
    workflow = WorkflowJob(
        id=f"wf_{method.value}",
        name=f"Workflow {method.value}",
        slug=method.value,
        source_json_path="workflows/x.json",
        output_dir="outputs/wf_test",
        pre_settings=_make_pre_settings(method),
        steps=[_make_step()],
    )
    assert workflow.pre_settings.model_creation.method == method
