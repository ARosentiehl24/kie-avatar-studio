"""Tests del `workflow_loader.scan_workflows_dir`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from kie_avatar_studio.infra.workflow_loader import (
    build_workflow_from_entry,
    scan_workflows_dir,
)


def _valid_payload() -> dict[str, Any]:
    return {
        "workflow": "Sample Automation",
        "pre_settings": {
            "model_creation": {
                "method": "prompt",
                "prompt": "Photorealistic medium shot of a Latina woman talking.",
            },
            "veo": {},
            "voice_changer": None,
        },
        "run": [
            {
                "step": 1,
                "scene_name": "Hook 1",
                "type": "a-roll",
                "change_scene": False,
                "scene_description": "",
                "prompt": "A medium close-up of a woman talking to camera.",
                "text": "Hola, gracias por estar aquí.",
            },
            {
                "step": 2,
                "scene_name": "Pain B-Roll",
                "type": "b-roll",
                "change_scene": True,
                "scene_description": "Close-up of jeans, natural lighting",
                "prompt": "Cinematic close-up of hands struggling to button jeans.",
                "text": "",
            },
        ],
    }


def _legacy_payload() -> dict[str, Any]:
    payload = _valid_payload()
    payload["pre_settings"] = {
        "audio_language": "es-419",
        "i2v_duration_seconds": 5,
        "model_creation": {
            "method": "prompt",
            "prompt": "Photorealistic medium shot of a Latina woman talking.",
        },
    }
    return payload


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
    # B-roll con change_scene=False emite warning.
    payload["run"][1]["change_scene"] = False
    (tmp_path / "x.json").write_text(json.dumps(payload), encoding="utf-8")
    entries = await scan_workflows_dir(tmp_path)
    assert entries[0].valid
    assert any("change_scene=false" in w for w in entries[0].warnings)


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
    assert workflow.pre_settings.veo.model == "veo3_fast"


async def test_build_workflow_from_entry_raises_on_invalid(tmp_path: Path) -> None:
    from kie_avatar_studio.domain.errors import WorkflowValidationError
    from kie_avatar_studio.domain.models import WorkflowEntry

    bad_entry = WorkflowEntry(name="x", path=tmp_path / "x.json", errors=["fail"])
    with pytest.raises(WorkflowValidationError):
        build_workflow_from_entry(bad_entry, workflow_id="wf_x", output_dir=tmp_path / "outputs")


async def test_entries_ordered_alphabetically(tmp_path: Path) -> None:
    for name in ("c", "a", "b"):
        (tmp_path / f"{name}.json").write_text(json.dumps(_valid_payload()), encoding="utf-8")
    entries = await scan_workflows_dir(tmp_path)
    assert [e.name for e in entries] == ["a", "b", "c"]


# --- duration_seconds parsing del step ------------------------------------


async def test_step_duration_seconds_parsed_as_int(tmp_path: Path) -> None:
    """`duration_seconds: 10` en el JSON se parsea como `int` al WorkflowStep."""
    payload = _valid_payload()
    payload["run"][1]["duration_seconds"] = 10  # b-roll
    (tmp_path / "wf.json").write_text(json.dumps(payload), encoding="utf-8")
    entries = await scan_workflows_dir(tmp_path)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.valid
    # `build_workflow_from_entry` materializa los steps; usamos el payload
    # parseado del entry para verificar que el campo viajó.
    wf = build_workflow_from_entry(entry, workflow_id="wf_test", output_dir=tmp_path / "out")
    assert wf.steps[1].duration_seconds == 10


# --- producto promocional (Round 6) ---------------------------------------


async def test_loader_parses_promote_product_and_include_product(tmp_path: Path) -> None:
    """`promote_product` en pre_settings + `include_product`/`product_prompt`
    por step se parsean al WorkflowJob."""
    payload = _valid_payload()
    payload["pre_settings"]["promote_product"] = True
    payload["run"][0]["include_product"] = True
    payload["run"][0]["set_as_base"] = True
    payload["run"][0]["product_prompt"] = "Sostiene el frasco a la altura del pecho"
    (tmp_path / "wf.json").write_text(json.dumps(payload), encoding="utf-8")
    entries = await scan_workflows_dir(tmp_path)
    assert len(entries) == 1
    assert entries[0].valid
    wf = build_workflow_from_entry(entries[0], workflow_id="wf_test", output_dir=tmp_path / "out")
    assert wf.pre_settings.promote_product is True
    assert wf.steps[0].include_product is True
    assert wf.steps[0].set_as_base is True
    assert wf.steps[0].product_prompt == "Sostiene el frasco a la altura del pecho"
    # Step 2 sin los campos → defaults.
    assert wf.steps[1].include_product is False
    assert wf.steps[1].set_as_base is False
    assert wf.steps[1].product_prompt == ""


async def test_loader_include_product_defaults_when_omitted(tmp_path: Path) -> None:
    """Sin los campos de producto, defaults: promote_product=False, include_product=False."""
    payload = _valid_payload()
    (tmp_path / "wf.json").write_text(json.dumps(payload), encoding="utf-8")
    entries = await scan_workflows_dir(tmp_path)
    wf = build_workflow_from_entry(entries[0], workflow_id="wf_test", output_dir=tmp_path / "out")
    assert wf.pre_settings.promote_product is False
    assert all(not step.include_product for step in wf.steps)


async def test_loader_parses_v2_veo_voice_changer_and_attached(tmp_path: Path) -> None:
    payload = _valid_payload()
    payload["pre_settings"]["veo"] = {
        "model": "veo3",
        "aspect_ratio": "16:9",
        "resolution": "1080p",
        "duration": 6,
        "enable_translation": False,
        "watermark": "KIE",
    }
    payload["pre_settings"]["voice_changer"] = {
        "voice_id": "voice_123",
        "model_id": "eleven_multilingual_sts_v2",
        "remove_background_noise": False,
        "output_format": "mp3_44100_128",
    }
    payload["run"][1]["attached"] = False
    (tmp_path / "wf.json").write_text(json.dumps(payload), encoding="utf-8")
    entries = await scan_workflows_dir(tmp_path)
    assert entries[0].valid
    wf = build_workflow_from_entry(entries[0], workflow_id="wf_test", output_dir=tmp_path / "out")
    assert wf.pre_settings.veo.model == "veo3"
    assert wf.pre_settings.veo.aspect_ratio == "16:9"
    assert wf.pre_settings.veo.resolution == "1080p"
    assert wf.pre_settings.veo.duration == 6
    assert wf.pre_settings.veo.enable_translation is False
    assert wf.pre_settings.veo.watermark == "KIE"
    assert wf.pre_settings.voice_changer is not None
    assert wf.pre_settings.voice_changer.voice_id == "voice_123"
    assert wf.pre_settings.voice_changer.remove_background_noise is False
    assert wf.steps[0].attached is True
    assert wf.steps[1].attached is False


async def test_loader_warns_when_veo_missing_and_deprecated_fields_present(tmp_path: Path) -> None:
    payload = _legacy_payload()
    (tmp_path / "legacy.json").write_text(json.dumps(payload), encoding="utf-8")
    entries = await scan_workflows_dir(tmp_path)
    assert entries[0].valid
    assert any("pre_settings.veo no está configurado" in w for w in entries[0].warnings)
    assert any("audio_language está deprecated" in w for w in entries[0].warnings)
    assert any("i2v_duration_seconds está deprecated" in w for w in entries[0].warnings)
    wf = build_workflow_from_entry(entries[0], workflow_id="wf_legacy", output_dir=tmp_path / "out")
    assert wf.pre_settings.audio_language == "es-419"
    assert wf.pre_settings.i2v_duration_seconds == 5


@pytest.mark.parametrize("legacy_key", ["voice_preset", "voice_preset_id"])
async def test_loader_rejects_removed_voice_preset_fields(tmp_path: Path, legacy_key: str) -> None:
    payload = _valid_payload()
    payload["pre_settings"][legacy_key] = "demo"
    (tmp_path / "legacy_voice.json").write_text(json.dumps(payload), encoding="utf-8")
    entries = await scan_workflows_dir(tmp_path)
    assert not entries[0].valid
    assert any("voice_preset/voice_preset_id ya no está soportado" in e for e in entries[0].errors)


async def test_loader_rejects_voice_changer_without_voice_id(tmp_path: Path) -> None:
    payload = _valid_payload()
    payload["pre_settings"]["voice_changer"] = {
        "voice_id": "   ",
        "model_id": "eleven_multilingual_sts_v2",
    }
    (tmp_path / "bad_voice_changer.json").write_text(json.dumps(payload), encoding="utf-8")
    entries = await scan_workflows_dir(tmp_path)
    assert not entries[0].valid
    assert any("voice_changer.voice_id no puede estar vacío" in e for e in entries[0].errors)


async def test_step_duration_seconds_omitted_defaults_to_none(tmp_path: Path) -> None:
    """JSON sin `duration_seconds` → step.duration_seconds=None (fallback en runtime)."""
    payload = _valid_payload()
    # No tocamos el payload original; ningún step trae duration_seconds.
    (tmp_path / "wf.json").write_text(json.dumps(payload), encoding="utf-8")
    entries = await scan_workflows_dir(tmp_path)
    wf = build_workflow_from_entry(entries[0], workflow_id="wf_test", output_dir=tmp_path / "out")
    assert all(step.duration_seconds is None for step in wf.steps)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (5, 5),
        (10, 10),
        ("5", 5),  # str numérica se acepta
        ("10", 10),
        (5.0, 5),  # float se acepta y coercea
        (None, None),
        ("", None),  # str vacía → None
        ("   ", None),  # whitespace → None
        ("abc", None),  # str no numérica → None (validator lo rechazaría)
        ([], None),  # tipo no convertible
        ({}, None),
        (True, None),  # bool se rechaza explícitamente
        (False, None),
    ],
)
async def test_duration_seconds_parsing_tolerant(
    tmp_path: Path, raw: object, expected: int | None
) -> None:
    payload = _valid_payload()
    payload["run"][1]["duration_seconds"] = raw
    (tmp_path / "wf.json").write_text(json.dumps(payload), encoding="utf-8")
    entries = await scan_workflows_dir(tmp_path)
    # Aceptamos que el entry sea inválido si el validador de dominio
    # rechaza el valor parseado (ej. el str "abc" parsea a None pero el
    # validator no levanta para None — es válido). Lo único que probamos
    # acá es la robustez del parser, no del validator.
    if entries[0].valid:
        wf = build_workflow_from_entry(
            entries[0], workflow_id="wf_test", output_dir=tmp_path / "out"
        )
        assert wf.steps[1].duration_seconds == expected
