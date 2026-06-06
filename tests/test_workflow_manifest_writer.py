"""Tests del `AtomicWorkflowManifestWriter`: atomic write + crash recovery."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

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
from kie_avatar_studio.infra.workflow_manifest_writer import (
    AtomicWorkflowManifestWriter,
    cleanup_stale_tmps,
)


def _make_workflow(output_dir: Path, *, status: WorkflowStatus = WorkflowStatus.QUEUED) -> WorkflowJob:
    return WorkflowJob(
        id="wf_test_001",
        name="Sample",
        slug="sample",
        source_json_path="workflows/sample.json",
        output_dir=str(output_dir),
        pre_settings=WorkflowPreSettings(
            audio_language="es-419",
            voice_preset_id="default",
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
                change_background=True,
                background_description="kitchen",
                prompt="prompt",
                text="",
            ),
        ],
        status=status,
    )


async def test_write_creates_workflow_json_with_expected_shape(tmp_path: Path) -> None:
    writer = AtomicWorkflowManifestWriter()
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
    assert len(data["steps"]) == 2
    assert data["steps"][0]["type"] == "a-roll"
    assert data["steps"][1]["type"] == "b-roll"
    # voice_preset usa el alias.
    assert data["pre_settings"]["voice_preset"] == "default"


async def test_write_creates_output_dir_if_missing(tmp_path: Path) -> None:
    writer = AtomicWorkflowManifestWriter()
    workflow = _make_workflow(tmp_path / "deep" / "nested" / "wf_001")
    ok = await writer.write(workflow)
    assert ok
    assert (tmp_path / "deep" / "nested" / "wf_001" / "workflow.json").is_file()


async def test_write_does_not_leave_tmp_file_after_success(tmp_path: Path) -> None:
    writer = AtomicWorkflowManifestWriter()
    workflow = _make_workflow(tmp_path / "wf_001")
    await writer.write(workflow)
    tmps = list((tmp_path / "wf_001").glob("workflow.json.*.tmp"))
    assert tmps == []


async def test_progress_summary_counts_states(tmp_path: Path) -> None:
    workflow = _make_workflow(tmp_path / "wf_001")
    workflow.steps[0].status = WorkflowStepStatus.COMPLETED
    workflow.steps[1].status = WorkflowStepStatus.RENDERING
    writer = AtomicWorkflowManifestWriter()
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
    writer = AtomicWorkflowManifestWriter()
    await writer.write(workflow)
    data = json.loads((tmp_path / "wf_001" / "workflow.json").read_text(encoding="utf-8"))
    assert data["steps"][0]["progress"] == {"scene_image": "completed", "audio": "running"}


async def test_outputs_only_include_set_paths(tmp_path: Path) -> None:
    workflow = _make_workflow(tmp_path / "wf_001")
    workflow.steps[0].scene_image_path = "outputs/wf/step_01/scene.png"
    workflow.steps[0].video_path = "outputs/wf/step_01/final.mp4"
    # step 1 no tiene audio_path → no debe aparecer en outputs.
    writer = AtomicWorkflowManifestWriter()
    await writer.write(workflow)
    data = json.loads((tmp_path / "wf_001" / "workflow.json").read_text(encoding="utf-8"))
    outputs = data["steps"][0]["outputs"]
    assert outputs == {
        "scene_image": "outputs/wf/step_01/scene.png",
        "video": "outputs/wf/step_01/final.mp4",
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
    writer = AtomicWorkflowManifestWriter()
    await writer.write(workflow)
    data = json.loads((tmp_path / "wf_001" / "workflow.json").read_text(encoding="utf-8"))
    assert data["model_base"] is not None
    assert data["model_base"]["kind"] == "generated"
    assert data["model_base"]["id"] == "img_xyz"


async def test_model_base_is_null_when_unresolved(tmp_path: Path) -> None:
    workflow = _make_workflow(tmp_path / "wf_001")
    writer = AtomicWorkflowManifestWriter()
    await writer.write(workflow)
    data = json.loads((tmp_path / "wf_001" / "workflow.json").read_text(encoding="utf-8"))
    assert data["model_base"] is None


async def test_write_overwrites_previous_manifest(tmp_path: Path) -> None:
    writer = AtomicWorkflowManifestWriter()
    workflow = _make_workflow(tmp_path / "wf_001")
    await writer.write(workflow)
    # Modificamos y reescribimos.
    workflow.status = WorkflowStatus.COMPLETED
    await writer.write(workflow)
    data = json.loads((tmp_path / "wf_001" / "workflow.json").read_text(encoding="utf-8"))
    assert data["status"] == "completed"


async def test_crash_before_replace_keeps_previous_intact(tmp_path: Path) -> None:
    """Si `replace` falla, el manifest previo sigue intacto (atomicidad simulada)."""
    writer = AtomicWorkflowManifestWriter()
    workflow = _make_workflow(tmp_path / "wf_001")
    await writer.write(workflow)
    target = tmp_path / "wf_001" / "workflow.json"
    original_payload = target.read_text(encoding="utf-8")

    # Simulamos crash en TODOS los intentos de replace: levantamos PermissionError persistente.
    workflow.status = WorkflowStatus.RUNNING

    def explode(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("simulated antivirus lock")

    with patch.object(Path, "replace", explode):
        ok = await writer.write(workflow)

    assert not ok
    # El manifest target sigue siendo el previo.
    assert target.read_text(encoding="utf-8") == original_payload
    # Tmps stale del intento fallido se limpiaron.
    tmps = list((tmp_path / "wf_001").glob("workflow.json.*.tmp"))
    assert tmps == []


async def test_replace_retries_on_transient_permission_error(tmp_path: Path) -> None:
    """Si `replace` falla 2 veces y al 3ro funciona, el write devuelve True."""
    writer = AtomicWorkflowManifestWriter()
    workflow = _make_workflow(tmp_path / "wf_001")

    # Mock: las primeras 2 llamadas levantan PermissionError, la 3ra OK.
    original_replace = Path.replace
    call_count = {"n": 0}

    def flaky_replace(self: Path, target: str | Path) -> Path:
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise PermissionError("flaky lock")
        return original_replace(self, target)

    with patch.object(Path, "replace", flaky_replace):
        ok = await writer.write(workflow)

    assert ok
    assert call_count["n"] == 3
    # Manifest final existe.
    assert (tmp_path / "wf_001" / "workflow.json").is_file()


async def test_cleanup_stale_tmps_removes_orphan_tmp_files(tmp_path: Path) -> None:
    output_dir = tmp_path / "wf_001"
    output_dir.mkdir()
    (output_dir / "workflow.json").write_text("{}")
    (output_dir / "workflow.json.abc12345.tmp").write_text("stale 1")
    (output_dir / "workflow.json.def67890.tmp").write_text("stale 2")
    # Un archivo no relacionado no se borra.
    (output_dir / "readme.txt").write_text("notes")

    removed = cleanup_stale_tmps(output_dir)
    assert removed == 2
    assert not (output_dir / "workflow.json.abc12345.tmp").exists()
    assert not (output_dir / "workflow.json.def67890.tmp").exists()
    assert (output_dir / "workflow.json").exists()
    assert (output_dir / "readme.txt").exists()


async def test_cleanup_stale_tmps_returns_zero_when_dir_missing(tmp_path: Path) -> None:
    assert cleanup_stale_tmps(tmp_path / "nope") == 0


async def test_concurrent_writes_to_same_workflow_produce_valid_json(tmp_path: Path) -> None:
    """Dos `write` simultáneos (mismo workflow) — el último gana, el JSON parsea OK.

    En producción esto no debe pasar (lock por workflow_id en runner) pero
    cada uno usa tmp único así que el peor caso es que el último que invoca
    `replace` quede como ganador. Verificamos que NO produce JSON corrupto.
    """
    writer = AtomicWorkflowManifestWriter()
    workflow_a = _make_workflow(tmp_path / "wf_001")
    workflow_b = _make_workflow(tmp_path / "wf_001")
    workflow_b.status = WorkflowStatus.RUNNING

    await asyncio.gather(writer.write(workflow_a), writer.write(workflow_b))

    # El JSON final debe ser parseable.
    data = json.loads((tmp_path / "wf_001" / "workflow.json").read_text(encoding="utf-8"))
    assert data["status"] in {"queued", "running"}


async def test_failed_write_does_not_raise_exception(tmp_path: Path) -> None:
    """Manifest failures NUNCA deben levantar (el runner debe poder seguir)."""
    writer = AtomicWorkflowManifestWriter()
    workflow = _make_workflow(tmp_path / "wf_001")
    # Simulamos disk full / path no escribible: mockeamos mkdir para que tire OSError.
    with patch.object(Path, "mkdir", side_effect=OSError("disk full")):
        ok = await writer.write(workflow)
    assert ok is False


async def test_failed_write_returns_false_when_payload_write_fails(tmp_path: Path) -> None:
    """Si falla `tmp.write_text`, devuelve False sin levantar."""
    writer = AtomicWorkflowManifestWriter()
    workflow = _make_workflow(tmp_path / "wf_001")
    with patch.object(Path, "write_text", side_effect=OSError("io error")):
        ok = await writer.write(workflow)
    assert ok is False
