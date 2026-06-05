from pathlib import Path

from kie_avatar_studio.infra.env_writer import DotenvWriter


def test_set_creates_file_with_value(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    writer = DotenvWriter(env)
    writer.set("FOO", "bar")
    content = env.read_text()
    assert "FOO" in content
    assert "bar" in content


def test_get_returns_persisted_value(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    writer = DotenvWriter(env)
    writer.set("HELLO", "world")
    assert writer.get("HELLO") == "world"


def test_get_missing_returns_none(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    writer = DotenvWriter(env)
    assert writer.get("MISSING") is None


def test_set_idempotent(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    writer = DotenvWriter(env)
    writer.set("KEY", "v1")
    writer.set("KEY", "v2")
    assert writer.get("KEY") == "v2"
    # solo aparece una línea con KEY (no se duplica)
    lines = [line for line in env.read_text().splitlines() if line.startswith("KEY")]
    assert len(lines) == 1


def test_unset_removes_key(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    writer = DotenvWriter(env)
    writer.set("X", "1")
    writer.unset("X")
    assert writer.get("X") is None


def test_set_creates_backup_when_file_exists(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("OLD=value\n")
    writer = DotenvWriter(env)
    writer.set("NEW", "value")
    backup = tmp_path / ".env.bak"
    assert backup.exists()
    assert "OLD=value" in backup.read_text()


def test_set_preserves_existing_comments(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("# top comment\nKEEP=original\n# bottom\n")
    writer = DotenvWriter(env)
    writer.set("KEEP", "modified")
    content = env.read_text()
    assert "# top comment" in content
    assert "# bottom" in content
