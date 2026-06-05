"""Configuración cargada desde .env vía pydantic-settings."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
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

    data_dir: Path = Path("./data")
    outputs_dir: Path = Path("./outputs")
    inputs_dir: Path = Path("./inputs")
    presets_dir: Path = Path("./presets")
    batch_jobs_dir: Path = Path("./batch_jobs")
    logs_dir: Path = Path("./logs")

    log_level: str = "INFO"

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
            self.logs_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
