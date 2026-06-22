from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from kie_avatar_studio.domain.models import (
    ModelCreation,
    ModelCreationMethod,
    StepType,
    WorkflowJob,
    WorkflowPreSettings,
    WorkflowStatus,
    WorkflowStep,
)
from kie_avatar_studio.infra.workflow_manifest_writer import AtomicWorkflowManifestWriter


def _make_workflow(output_dir: Path) -> WorkflowJob:
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
            )
        ],
        status=WorkflowStatus.QUEUED,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


async def test_write_overwrites_previous_manifest(tmp_path: Path) -> None:
    writer = AtomicWorkflowManifestWriter(tmp_path)
    workflow = _make_workflow(tmp_path / "wf_001")
    await writer.write(workflow)
    workflow.status = WorkflowStatus.COMPLETED
    await writer.write(workflow)
    data = json.loads((tmp_path / "wf_001" / "workflow.json").read_text(encoding="utf-8"))
    assert data["status"] == "completed"


async def test_crash_before_replace_keeps_previous_intact(tmp_path: Path) -> None:
    writer = AtomicWorkflowManifestWriter(tmp_path)
    workflow = _make_workflow(tmp_path / "wf_001")
    await writer.write(workflow)
    target = tmp_path / "wf_001" / "workflow.json"
    original_payload = target.read_text(encoding="utf-8")
    workflow.status = WorkflowStatus.RUNNING

    def explode(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("simulated antivirus lock")

    with patch.object(Path, "replace", explode):
        ok = await writer.write(workflow)

    assert not ok
    assert target.read_text(encoding="utf-8") == original_payload
    assert list((tmp_path / "wf_001").glob("workflow.json.*.tmp")) == []


async def test_replace_retries_on_transient_permission_error(tmp_path: Path) -> None:
    writer = AtomicWorkflowManifestWriter(tmp_path)
    workflow = _make_workflow(tmp_path / "wf_001")
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
    assert (tmp_path / "wf_001" / "workflow.json").is_file()


async def test_concurrent_writes_to_same_workflow_produce_valid_json(tmp_path: Path) -> None:
    writer = AtomicWorkflowManifestWriter(tmp_path)
    workflow_a = _make_workflow(tmp_path / "wf_001")
    workflow_b = _make_workflow(tmp_path / "wf_001")
    workflow_b.status = WorkflowStatus.RUNNING

    await asyncio.gather(writer.write(workflow_a), writer.write(workflow_b))

    data = json.loads((tmp_path / "wf_001" / "workflow.json").read_text(encoding="utf-8"))
    assert data["status"] in {"queued", "running"}


async def test_failed_write_does_not_raise_exception(tmp_path: Path) -> None:
    writer = AtomicWorkflowManifestWriter(tmp_path)
    workflow = _make_workflow(tmp_path / "wf_001")
    with patch.object(Path, "mkdir", side_effect=OSError("disk full")):
        ok = await writer.write(workflow)
    assert ok is False


async def test_failed_write_returns_false_when_payload_write_fails(tmp_path: Path) -> None:
    writer = AtomicWorkflowManifestWriter(tmp_path)
    workflow = _make_workflow(tmp_path / "wf_001")
    with patch.object(Path, "write_text", side_effect=OSError("io error")):
        ok = await writer.write(workflow)
    assert ok is False
