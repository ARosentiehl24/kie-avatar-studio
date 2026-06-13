from __future__ import annotations

from pathlib import Path

from kie_avatar_studio.app_layer.runtime_state_cleaner import RuntimeStateCleaner, runtime_db_files


async def test_runtime_state_cleaner_removes_only_sqlite_files(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "jobs.db"
    db_path.parent.mkdir()
    keys_path = tmp_path / "data" / "keys.json"
    outputs_dir = tmp_path / "outputs"
    outputs_dir.mkdir()
    keys_path.write_text('{"keys":[]}', encoding="utf-8")
    for path in runtime_db_files(db_path):
        path.write_text("db", encoding="utf-8")

    result = await RuntimeStateCleaner(db_path).cleanup()

    assert set(result.removed) == set(runtime_db_files(db_path))
    assert keys_path.exists()
    assert outputs_dir.exists()
    assert all(not path.exists() for path in runtime_db_files(db_path))


async def test_runtime_state_cleaner_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.db"
    result = await RuntimeStateCleaner(db_path).cleanup()
    assert result.removed == ()
    assert set(result.missing) == set(runtime_db_files(db_path))
