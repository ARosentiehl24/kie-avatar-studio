"""Tests del `AtomicWorkflowManifestWriter`: atomic write + crash recovery."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from kie_avatar_studio.domain.models import (
    ImageAssetKind,
    ImageAssetRef,
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
from kie_avatar_studio.infra.workflow_manifest_writer import AtomicWorkflowManifestWriter


def _make_workflow(
    output_dir: Path, *, status: WorkflowStatus = WorkflowStatus.QUEUED
) -> WorkflowJob:
    return WorkflowJob(
        id="wf_test_001",
        name="Sample",
        slug="sample",
        source_json_path="workflows/sample.json",
        output_dir=str(output_dir),
        pre_settings=WorkflowPreSettings(
            audio_language="es-419",
            model_creation=ModelCreation(method=ModelCreationMethod.PROMPT, prompt="A woman"),
        ),
        steps=[
            WorkflowStep(
                step=1,
                scene_name="Hook",
                scene_slug="hook",
                type=StepType.A_ROLL,
                prompt="prompt",
                text="hola mundo",
            ),
            WorkflowStep(
                step=2,
                scene_name="B-roll",
                scene_slug="b_roll",
                type=StepType.B_ROLL,
                change_scene=True,
                scene_description="kitchen",
                prompt="prompt",
                text="",
            ),
        ],
        status=status,
    )


async def test_write_creates_workflow_json_with_expected_shape(tmp_path: Path) -> None:
    writer = AtomicWorkflowManifestWriter(tmp_path)
    workflow = _make_workflow(tmp_path / "wf_001")
    ok = await writer.write(workflow)
    assert ok
    target = tmp_path / "wf_001" / "workflow.json"
    assert target.is_file()
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["id"] == "wf_test_001"
    assert data["status"] == "queued"
    assert data["name"] == "Sample"
    assert data["slug"] == "sample"
    assert data["error"] is None
    assert data["manifest_write_failed"] is False
    assert data["outputs"] == {}
    assert len(data["steps"]) == 2
    assert data["steps"][0]["type"] == "a-roll"
    assert data["steps"][0]["set_as_base"] is False
    assert data["steps"][1]["type"] == "b-roll"
    assert data["pre_settings"]["audio_language"] == "es-419"


async def test_write_creates_output_dir_if_missing(tmp_path: Path) -> None:
    writer = AtomicWorkflowManifestWriter(tmp_path)
    workflow = _make_workflow(tmp_path / "deep" / "nested" / "wf_001")
    ok = await writer.write(workflow)
    assert ok
    assert (tmp_path / "deep" / "nested" / "wf_001" / "workflow.json").is_file()


async def test_write_rejects_output_dir_outside_outputs_dir(tmp_path: Path) -> None:
    writer = AtomicWorkflowManifestWriter(tmp_path / "outputs")
    outside = tmp_path / "outside" / "wf_001"
    workflow = _make_workflow(outside)
    ok = await writer.write(workflow)
    assert not ok
    assert not outside.exists()


async def test_write_does_not_leave_tmp_file_after_success(tmp_path: Path) -> None:
    writer = AtomicWorkflowManifestWriter(tmp_path)
    workflow = _make_workflow(tmp_path / "wf_001")
    await writer.write(workflow)
    tmps = list((tmp_path / "wf_001").glob("workflow.json.*.tmp"))
    assert tmps == []


async def test_progress_summary_counts_states(tmp_path: Path) -> None:
    workflow = _make_workflow(tmp_path / "wf_001")
    workflow.steps[0].status = WorkflowStepStatus.COMPLETED
    workflow.steps[1].status = WorkflowStepStatus.RENDERING
    writer = AtomicWorkflowManifestWriter(tmp_path)
    await writer.write(workflow)
    data = json.loads((tmp_path / "wf_001" / "workflow.json").read_text(encoding="utf-8"))
    assert "1 completados" in data["progress_summary"]
    assert "1 en curso" in data["progress_summary"]
    assert "de 2" in data["progress_summary"]


async def test_progress_dict_serializes_with_enum_values(tmp_path: Path) -> None:
    workflow = _make_workflow(tmp_path / "wf_001")
    workflow.steps[0].progress = {
        WorkflowProgressKey.SCENE_IMAGE: WorkflowProgressStatus.COMPLETED,
        WorkflowProgressKey.AUDIO: WorkflowProgressStatus.RUNNING,
    }
    writer = AtomicWorkflowManifestWriter(tmp_path)
    await writer.write(workflow)
    data = json.loads((tmp_path / "wf_001" / "workflow.json").read_text(encoding="utf-8"))
    assert data["steps"][0]["progress"] == {"scene_image": "completed", "audio": "running"}


async def test_outputs_only_include_set_paths(tmp_path: Path) -> None:
    workflow = _make_workflow(tmp_path / "wf_001")
    workflow.steps[0].scene_image_path = "outputs/wf/step_01/scene.png"
    workflow.steps[0].video_path = "outputs/wf/step_01/final.mp4"
    # step 1 no tiene audio_path → no debe aparecer en outputs.
    writer = AtomicWorkflowManifestWriter(tmp_path)
    await writer.write(workflow)
    data = json.loads((tmp_path / "wf_001" / "workflow.json").read_text(encoding="utf-8"))
    outputs = data["steps"][0]["outputs"]
    assert outputs == {
        "scene_image": "outputs/wf/step_01/scene.png",
        "video": "outputs/wf/step_01/final.mp4",
    }


async def test_workflow_outputs_include_existing_final_artifacts(tmp_path: Path) -> None:
    workflow = _make_workflow(tmp_path / "wf_001")
    output_dir = Path(workflow.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "final.mp4").write_bytes(b"video")
    (output_dir / "final_audio.mp3").write_bytes(b"audio")
    (output_dir / "voice_changed_audio.mp3").write_bytes(b"changed")
    writer = AtomicWorkflowManifestWriter(tmp_path)
    await writer.write(workflow)
    data = json.loads((tmp_path / "wf_001" / "workflow.json").read_text(encoding="utf-8"))
    assert data["outputs"] == {
        "video": str(output_dir / "final.mp4"),
        "audio": str(output_dir / "final_audio.mp3"),
        "voice_changed_audio": str(output_dir / "voice_changed_audio.mp3"),
    }


async def test_model_base_block_when_resolved(tmp_path: Path) -> None:
    workflow = _make_workflow(tmp_path / "wf_001")
    workflow.pre_settings.model_creation.resolved_image_ref = ImageAssetRef(
        kind=ImageAssetKind.GENERATED,
        id="img_xyz",
        label="modelo base",
        kie_url="https://tempfile.kie.ai/base.png",
        expires_at=datetime.now(UTC),
    )
    writer = AtomicWorkflowManifestWriter(tmp_path)
    await writer.write(workflow)
    data = json.loads((tmp_path / "wf_001" / "workflow.json").read_text(encoding="utf-8"))
    assert data["model_base"] is not None
    assert data["model_base"]["kind"] == "generated"
    assert data["model_base"]["id"] == "img_xyz"


async def test_model_base_is_null_when_unresolved(tmp_path: Path) -> None:
    workflow = _make_workflow(tmp_path / "wf_001")
    writer = AtomicWorkflowManifestWriter(tmp_path)
    await writer.write(workflow)
    data = json.loads((tmp_path / "wf_001" / "workflow.json").read_text(encoding="utf-8"))
    assert data["model_base"] is None


async def test_product_block_when_promoting(tmp_path: Path) -> None:
    from kie_avatar_studio.domain.models import ProductImage

    workflow = _make_workflow(tmp_path / "wf_001")
    workflow.pre_settings.promote_product = True
    workflow.pre_settings.product_image = ProductImage(
        local_path="inputs/product.png",
        resolved_image_ref=ImageAssetRef(
            kind=ImageAssetKind.UPLOADED,
            id="uploads/product.png",
            label="product.png",
            kie_url="https://tempfile.kie.ai/product.png",
            expires_at=datetime.now(UTC),
        ),
    )
    workflow.steps[1].include_product = True
    workflow.steps[1].product_prompt = "Jar centered on the table"
    writer = AtomicWorkflowManifestWriter(tmp_path)
    await writer.write(workflow)
    data = json.loads((tmp_path / "wf_001" / "workflow.json").read_text(encoding="utf-8"))
    assert data["product"] is not None
    assert data["product"]["kind"] == "uploaded"
    assert data["product"]["kie_url"] == "https://tempfile.kie.ai/product.png"
    assert data["product"]["local_path"] == "inputs/product.png"
    # Los campos del step también se serializan.
    assert data["steps"][1]["include_product"] is True
    assert data["steps"][1]["product_prompt"] == "Jar centered on the table"


async def test_product_block_is_null_when_not_promoting(tmp_path: Path) -> None:
    workflow = _make_workflow(tmp_path / "wf_001")
    writer = AtomicWorkflowManifestWriter(tmp_path)
    await writer.write(workflow)
    data = json.loads((tmp_path / "wf_001" / "workflow.json").read_text(encoding="utf-8"))
    assert data["product"] is None
    assert data["steps"][0]["include_product"] is False
