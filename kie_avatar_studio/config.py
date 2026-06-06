r"""Configuración cargada desde .env vía pydantic-settings.

Cuando la app corre como .exe empaquetado con PyInstaller (`sys.frozen`),
los paths de datos NO pueden ser relativos al CWD: el .exe normalmente se
instala en `Program Files` (no writable para usuarios no-admin) y se
lanza desde shortcuts cuyo CWD puede ser cualquier cosa (típicamente el
propio `Program Files\Kie Avatar Studio\`). En ese caso caemos a la
convención per-OS de "user data dir":

    Windows: %LOCALAPPDATA%\KieAvatarStudio\
    macOS:   ~/Library/Application Support/KieAvatarStudio/
    Linux:   $XDG_DATA_HOME/KieAvatarStudio/  (o ~/.local/share/...)

En modo dev (`python -m kie_avatar_studio` o `pytest`) NO estamos frozen,
así que mantenemos paths relativos al CWD para preservar el workflow del
repo (`data/`, `outputs/`, `logs/`, ...). Los tests inyectan paths
explícitos vía `Settings(data_dir=tmp_path / "data", ...)`, así que esta
lógica no los afecta.

El `.env` queda junto al resto del estado por la misma razón: el
`DotenvWriter` lo resuelve como `settings.data_dir.parent / ".env"`
en `app.py`, así que con `data_dir` bien resuelto el `.env` queda donde
corresponde sin tocar más código.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_APP_DIR_NAME = "KieAvatarStudio"


def _is_frozen() -> bool:
    """True cuando la app corre como bundle de PyInstaller (.exe)."""
    return bool(getattr(sys, "frozen", False))


def _app_data_root() -> Path:
    """Directorio base writable per-usuario para datos de la app.

    Cuando NO está frozen devuelve `Path()` (CWD) para preservar el
    comportamiento histórico (paths relativos al directorio del repo).
    """
    if not _is_frozen():
        return Path()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
        return root / _APP_DIR_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / _APP_DIR_NAME
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / _APP_DIR_NAME


def _env_file_path() -> Path | str:
    """Path del `.env` que pydantic-settings va a leer al instanciar `Settings`.

    En modo frozen vive en `_app_data_root() / ".env"`. En dev devolvemos
    el string literal `".env"` para que pydantic-settings resuelva relativo
    al CWD (sin tocar el comportamiento existente del repo).
    """
    if _is_frozen():
        return _app_data_root() / ".env"
    return ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    kie_api_key: str = Field(default="", description="API key Bearer para Kie.ai")
    kie_api_base: str = "https://api.kie.ai"
    kie_upload_base: str = "https://kieai.redpandaai.co"

    max_parallel_jobs: int = 2
    poll_interval_seconds: int = 10
    task_timeout_seconds: int = 1800

    default_voice: str = "EkK5I93UQWFDigLMpZcX"
    default_prompt: str = "Mirada a cámara, expresión natural, gestos suaves, tono confiado."

    notifications_enabled: bool = True
    update_check_enabled: bool = True
    update_check_repo: str = "ARosentiehl24/kie-avatar-studio"

    # `default_factory` (no `default`) para que los paths se resuelvan en
    # cada instanciación de `Settings()`. Así los tests que monkeypatchean
    # `sys.frozen` ven los paths correctos por test, en vez de un valor
    # congelado al import time del módulo.
    data_dir: Path = Field(default_factory=lambda: _app_data_root() / "data")
    outputs_dir: Path = Field(default_factory=lambda: _app_data_root() / "outputs")
    inputs_dir: Path = Field(default_factory=lambda: _app_data_root() / "inputs")
    presets_dir: Path = Field(default_factory=lambda: _app_data_root() / "presets")
    batch_jobs_dir: Path = Field(default_factory=lambda: _app_data_root() / "batch_jobs")
    workflows_dir: Path = Field(default_factory=lambda: _app_data_root() / "workflows")
    logs_dir: Path = Field(default_factory=lambda: _app_data_root() / "logs")

    log_level: str = "INFO"

    max_parallel_workflows: int = 1

    def __init__(self, **data: Any) -> None:
        # Resolvemos `env_file` en cada instanciación (no al import time
        # como haría declarando `env_file=_env_file_path()` en
        # `SettingsConfigDict`): si el caller no lo pasó explícitamente,
        # usamos el path correcto según `sys.frozen`. Esto mantiene la
        # simetría con los `default_factory` de los `*_dir`.
        if "_env_file" not in data:
            data["_env_file"] = _env_file_path()
        super().__init__(**data)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "jobs.db"

    def ensure_dirs(self) -> None:
        for d in (
            self.data_dir,
            self.outputs_dir,
            self.inputs_dir,
            self.presets_dir,
            self.batch_jobs_dir,
            self.workflows_dir,
            self.logs_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
