"""Tests del `BatchLoader`: scan + parsing + errores de carpetas batch_jobs."""

from __future__ import annotations

import json
from pathlib import Path

from kie_avatar_studio.infra.batch_loader import scan_batch_dir


def _make_image(path: Path, *, size: int = 256) -> None:
    """Escribe un PNG mínimo válido para `validate_image_path` (no-empty)."""
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * size)


async def test_scan_returns_empty_when_dir_missing(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    entries = await scan_batch_dir(missing, default_prompt="P", default_voice="V")
    assert entries == []


async def test_scan_returns_empty_when_dir_is_empty(tmp_path: Path) -> None:
    batch = tmp_path / "batch_jobs"
    batch.mkdir()
    entries = await scan_batch_dir(batch, default_prompt="P", default_voice="V")
    assert entries == []


async def test_scan_returns_valid_entry(tmp_path: Path) -> None:
    folder = tmp_path / "video_001"
    folder.mkdir()
    (folder / "script.txt").write_text("hola mundo")
    _make_image(folder / "modelo.png")
    entries = await scan_batch_dir(tmp_path, default_prompt="P_default", default_voice="V_default")
    assert len(entries) == 1
    entry = entries[0]
    assert entry.name == "video_001"
    assert entry.valid is True
    assert entry.script == "hola mundo"
    assert entry.image_path == folder / "modelo.png"
    assert entry.prompt == "P_default"
    assert entry.voice == "V_default"
    assert entry.errors == []


async def test_scan_uses_explicit_voice_and_prompt_files(tmp_path: Path) -> None:
    folder = tmp_path / "video_001"
    folder.mkdir()
    (folder / "script.txt").write_text("script")
    _make_image(folder / "modelo.jpg")
    (folder / "voice.txt").write_text("VOICE_ABC")
    (folder / "prompt.txt").write_text("custom prompt")
    entries = await scan_batch_dir(tmp_path, default_prompt="P_def", default_voice="V_def")
    assert entries[0].voice == "VOICE_ABC"
    assert entries[0].prompt == "custom prompt"


async def test_scan_meta_json_overrides_txt_files(tmp_path: Path) -> None:
    folder = tmp_path / "v"
    folder.mkdir()
    (folder / "script.txt").write_text("s")
    _make_image(folder / "modelo.png")
    (folder / "voice.txt").write_text("FROM_TXT")
    (folder / "meta.json").write_text(json.dumps({"voice": "FROM_META", "prompt": "META_P"}))
    entries = await scan_batch_dir(tmp_path, default_prompt="x", default_voice="x")
    assert entries[0].voice == "FROM_META"
    assert entries[0].prompt == "META_P"


async def test_scan_meta_json_partial_keeps_txt_fallback(tmp_path: Path) -> None:
    """meta.json con solo `voice` deja el `prompt` venir de prompt.txt."""
    folder = tmp_path / "v"
    folder.mkdir()
    (folder / "script.txt").write_text("s")
    _make_image(folder / "modelo.png")
    (folder / "prompt.txt").write_text("PROMPT_TXT")
    (folder / "meta.json").write_text(json.dumps({"voice": "META_V"}))
    entries = await scan_batch_dir(tmp_path, default_prompt="D", default_voice="D")
    assert entries[0].voice == "META_V"
    assert entries[0].prompt == "PROMPT_TXT"


async def test_scan_reports_missing_script(tmp_path: Path) -> None:
    folder = tmp_path / "v"
    folder.mkdir()
    _make_image(folder / "modelo.png")
    entries = await scan_batch_dir(tmp_path, default_prompt="P", default_voice="V")
    assert entries[0].valid is False
    assert any("script" in e for e in entries[0].errors)


async def test_scan_reports_missing_image(tmp_path: Path) -> None:
    folder = tmp_path / "v"
    folder.mkdir()
    (folder / "script.txt").write_text("hola")
    entries = await scan_batch_dir(tmp_path, default_prompt="P", default_voice="V")
    assert entries[0].valid is False
    assert any("modelo" in e for e in entries[0].errors)


async def test_scan_reports_invalid_meta_json(tmp_path: Path) -> None:
    folder = tmp_path / "v"
    folder.mkdir()
    (folder / "script.txt").write_text("s")
    _make_image(folder / "modelo.png")
    (folder / "meta.json").write_text("{not valid json")
    entries = await scan_batch_dir(tmp_path, default_prompt="P", default_voice="V")
    assert entries[0].valid is False
    assert any("meta.json" in e for e in entries[0].errors)


async def test_scan_meta_json_must_be_object(tmp_path: Path) -> None:
    folder = tmp_path / "v"
    folder.mkdir()
    (folder / "script.txt").write_text("s")
    _make_image(folder / "modelo.png")
    (folder / "meta.json").write_text(json.dumps(["not", "an", "object"]))
    entries = await scan_batch_dir(tmp_path, default_prompt="P", default_voice="V")
    assert entries[0].valid is False
    assert any("objeto" in e for e in entries[0].errors)


async def test_scan_orders_entries_alphabetically(tmp_path: Path) -> None:
    for name in ("video_003", "video_001", "video_002"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "script.txt").write_text("s")
        _make_image(tmp_path / name / "modelo.png")
    entries = await scan_batch_dir(tmp_path, default_prompt="P", default_voice="V")
    assert [e.name for e in entries] == ["video_001", "video_002", "video_003"]


async def test_scan_ignores_loose_files_in_batch_root(tmp_path: Path) -> None:
    """Solo subcarpetas son entries. Archivos sueltos en el root se ignoran."""
    (tmp_path / "README.md").write_text("hello")
    (tmp_path / "video_001").mkdir()
    (tmp_path / "video_001" / "script.txt").write_text("s")
    _make_image(tmp_path / "video_001" / "modelo.png")
    entries = await scan_batch_dir(tmp_path, default_prompt="P", default_voice="V")
    assert [e.name for e in entries] == ["video_001"]


async def test_scan_empty_script_file_treated_as_missing(tmp_path: Path) -> None:
    """script.txt vacío o solo whitespace cuenta como faltante."""
    folder = tmp_path / "v"
    folder.mkdir()
    (folder / "script.txt").write_text("   \n  ")
    _make_image(folder / "modelo.png")
    entries = await scan_batch_dir(tmp_path, default_prompt="P", default_voice="V")
    assert entries[0].valid is False
