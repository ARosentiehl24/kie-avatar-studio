"""Tests del `workflow_loader.scan_workflows_dir`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kie_avatar_studio.infra.workflow_loader import (
    build_workflow_from_entry,
    scan_workflows_dir,
)


def _valid_payload() -> dict:
    return {
        "workflow": "Sample Automation",
        "pre_settings": {
            "audio_language": "es-419",
            "voice_preset": "latina_warm_authentic",
            "model_creation": {
                "method": "prompt",
                "prompt": "Photorealistic medium shot of a Latina woman talking.",
            },
        },
        "run": [
            {
                "step": 1,
                "scene_name": "Hook 1",
                "type": "a-roll",
                "change_background": False,
                "background_description": "",
                "prompt": "A medium close-up of a woman talking to camera.",
                "text": "Hola, gracias por estar aquí.",
            },
            {
                "step": 2,
                "scene_name": "Pain B-Roll",
                "type": "b-roll",
                "change_background": True,
                "background_description": "Close-up of jeans, natural lighting",
                "prompt": "Cinematic close-up of hands struggling to button jeans.",
                "text": "",
            },
        ],
    }


async def test_returns_empty_list_when_dir_missing(tmp_path: Path) -> None:
    entries = await scan_workflows_dir(tmp_path / "nope")
    assert entries == []


async def test_returns_empty_list_when_dir_has_no_jsons(tmp_path: Path) -> None:
    (tmp_path / "readme.txt").write_text("not a workflow")
    entries = await scan_workflows_dir(tmp_path)
    assert entries == []


async def test_parses_valid_workflow(tmp_path: Path) -> None:
    (tmp_path / "demo.json").write_text(json.dumps(_valid_payload()), encoding="utf-8")
    entries = await scan_workflows_dir(tmp_path)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.valid
    assert entry.errors == []
    assert entry.warnings == []
    assert entry.workflow_payload is not None


async def test_reports_json_parse_error(tmp_path: Path) -> None:
    (tmp_path / "broken.json").write_text("{not valid json", encoding="utf-8")
    entries = await scan_workflows_dir(tmp_path)
    assert len(entries) == 1
    assert not entries[0].valid
    assert any("JSON inválido" in e for e in entries[0].errors)


async def test_reports_non_object_root(tmp_path: Path) -> None:
    (tmp_path / "array.json").write_text("[1,2,3]", encoding="utf-8")
    entries = await scan_workflows_dir(tmp_path)
    assert not entries[0].valid
    assert any("objeto en la raíz" in e for e in entries[0].errors)


async def test_reports_missing_pre_settings_fields(tmp_path: Path) -> None:
    bad = {"workflow": "Bad", "pre_settings": {}, "run": []}
    (tmp_path / "bad.json").write_text(json.dumps(bad), encoding="utf-8")
    entries = await scan_workflows_dir(tmp_path)
    assert not entries[0].valid
    assert any("pre_settings" in e for e in entries[0].errors)


async def test_reports_non_consecutive_step_numbers(tmp_path: Path) -> None:
    payload = _valid_payload()
    payload["run"][1]["step"] = 5  # crea un gap
    (tmp_path / "x.json").write_text(json.dumps(payload), encoding="utf-8")
    entries = await scan_workflows_dir(tmp_path)
    assert not entries[0].valid
    assert any("consecutivos" in e for e in entries[0].errors)


async def test_does_not_block_valid_entries_when_one_is_broken(tmp_path: Path) -> None:
    (tmp_path / "good.json").write_text(json.dumps(_valid_payload()), encoding="utf-8")
    (tmp_path / "broken.json").write_text("{nope", encoding="utf-8")
    entries = await scan_workflows_dir(tmp_path)
    assert len(entries) == 2
    valid = [e for e in entries if e.valid]
    invalid = [e for e in entries if not e.valid]
    assert len(valid) == 1
    assert len(invalid) == 1


async def test_collects_warnings_without_blocking(tmp_path: Path) -> None:
    payload = _valid_payload()
    # B-roll con change_background=False emite warning.
    payload["run"][1]["change_background"] = False
    (tmp_path / "x.json").write_text(json.dumps(payload), encoding="utf-8")
    entries = await scan_workflows_dir(tmp_path)
    assert entries[0].valid
    assert any("change_background=false" in w for w in entries[0].warnings)


async def test_build_workflow_from_entry_assigns_id_and_output_dir(tmp_path: Path) -> None:
    (tmp_path / "demo.json").write_text(json.dumps(_valid_payload()), encoding="utf-8")
    entries = await scan_workflows_dir(tmp_path)
    workflow = build_workflow_from_entry(
        entries[0],
        workflow_id="wf_20260605_abc",
        output_dir=tmp_path / "outputs" / "wf_20260605_abc",
    )
    assert workflow.id == "wf_20260605_abc"
    assert workflow.output_dir == str(tmp_path / "outputs" / "wf_20260605_abc")
    assert workflow.slug == "sample_automation"
    assert len(workflow.steps) == 2
    assert workflow.pre_settings.voice_preset_id == "latina_warm_authentic"


async def test_build_workflow_from_entry_raises_on_invalid(tmp_path: Path) -> None:
    from kie_avatar_studio.domain.errors import WorkflowValidationError
    from kie_avatar_studio.domain.models import WorkflowEntry

    bad_entry = WorkflowEntry(name="x", path=tmp_path / "x.json", errors=["fail"])
    with pytest.raises(WorkflowValidationError):
        build_workflow_from_entry(
            bad_entry, workflow_id="wf_x", output_dir=tmp_path / "outputs"
        )


async def test_entries_ordered_alphabetically(tmp_path: Path) -> None:
    for name in ("c", "a", "b"):
        (tmp_path / f"{name}.json").write_text(json.dumps(_valid_payload()), encoding="utf-8")
    entries = await scan_workflows_dir(tmp_path)
    assert [e.name for e in entries] == ["a", "b", "c"]
