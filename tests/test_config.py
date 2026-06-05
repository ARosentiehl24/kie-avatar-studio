"""Tests de `kie_avatar_studio.config`: resolución de paths según modo frozen.

Estos tests aseguran que:

1. En modo dev (no `sys.frozen`) los defaults siguen relativos al CWD —
   crítico para no romper el workflow del repo ni el resto de la suite.
2. Cuando la app corre como .exe de PyInstaller (`sys.frozen=True`)
   los defaults caen al "user data dir" del SO en vez de intentar
   escribir en CWD (que sería `Program Files\\Kie Avatar Studio\\`
   tras el instalador Inno Setup → falla por permisos sin admin).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from kie_avatar_studio.config import (
    Settings,
    _app_data_root,
    _env_file_path,
    _is_frozen,
)

# ---------------------------------------------------------------------------
# Modo dev (no frozen) — debe preservar comportamiento histórico.
# ---------------------------------------------------------------------------


def test_is_frozen_false_in_dev_mode() -> None:
    assert _is_frozen() is False


def test_app_data_root_returns_cwd_dot_in_dev_mode() -> None:
    assert _app_data_root() == Path()


def test_env_file_path_is_relative_string_in_dev_mode() -> None:
    # pydantic-settings resuelve `".env"` (str) relativo al CWD.
    # Devolverlo como string es lo que mantiene el comportamiento previo.
    assert _env_file_path() == ".env"


def test_settings_defaults_relative_to_cwd_in_dev_mode() -> None:
    s = Settings()
    assert s.data_dir == Path("data")
    assert s.outputs_dir == Path("outputs")
    assert s.inputs_dir == Path("inputs")
    assert s.presets_dir == Path("presets")
    assert s.batch_jobs_dir == Path("batch_jobs")
    assert s.logs_dir == Path("logs")


# ---------------------------------------------------------------------------
# Modo frozen (PyInstaller .exe) — debe redirigir al user data dir per-OS.
# ---------------------------------------------------------------------------


@pytest.fixture
def frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simula la app corriendo como bundle de PyInstaller."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)


def test_is_frozen_true_when_sys_frozen_set(frozen: None) -> None:
    assert _is_frozen() is True


def test_app_data_root_uses_localappdata_on_windows(
    frozen: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert _app_data_root() == tmp_path / "KieAvatarStudio"


def test_app_data_root_falls_back_to_home_when_localappdata_missing(
    frozen: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    expected = tmp_path / "AppData" / "Local" / "KieAvatarStudio"
    assert _app_data_root() == expected


def test_app_data_root_uses_library_app_support_on_macos(
    frozen: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    expected = tmp_path / "Library" / "Application Support" / "KieAvatarStudio"
    assert _app_data_root() == expected


def test_app_data_root_uses_xdg_data_home_on_linux(
    frozen: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    assert _app_data_root() == tmp_path / "KieAvatarStudio"


def test_app_data_root_falls_back_to_dot_local_share_on_linux(
    frozen: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    expected = tmp_path / ".local" / "share" / "KieAvatarStudio"
    assert _app_data_root() == expected


def test_env_file_path_lives_in_app_data_root_when_frozen(
    frozen: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert _env_file_path() == tmp_path / "KieAvatarStudio" / ".env"


def test_settings_defaults_use_app_data_root_when_frozen(
    frozen: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    s = Settings()
    root = tmp_path / "KieAvatarStudio"
    assert s.data_dir == root / "data"
    assert s.outputs_dir == root / "outputs"
    assert s.inputs_dir == root / "inputs"
    assert s.presets_dir == root / "presets"
    assert s.batch_jobs_dir == root / "batch_jobs"
    assert s.logs_dir == root / "logs"


def test_env_writer_path_under_app_data_root_when_frozen(
    frozen: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verifica el contrato implícito entre `config.py` y `app.py:150`.

    `app.py` resuelve la ruta del `.env` como `settings.data_dir.parent /
    ".env"`. Con `data_dir = <app_data_root>/data`, el `.env` queda en
    `<app_data_root>/.env`, que es exactamente lo que necesitamos al
    instalar en Program Files.
    """
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    s = Settings()
    assert s.data_dir.parent / ".env" == tmp_path / "KieAvatarStudio" / ".env"
