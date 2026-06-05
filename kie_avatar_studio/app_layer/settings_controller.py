"""Controller para los settings persistidos en `.env` (no-keys).

Cubre:
- Endpoints (`KIE_API_BASE`, `KIE_UPLOAD_BASE`).
- Paralelismo (`MAX_PARALLEL_JOBS`).
- Polling (`POLL_INTERVAL_SECONDS`, `TASK_TIMEOUT_SECONDS`).
- Defaults (`DEFAULT_VOICE`, `DEFAULT_PROMPT`).

Lee el valor "vivo" desde `Settings` (que ya cargó el `.env`) y escribe a través
del `EnvWriter` para preservar formato y comments. Tras guardar, el caller
(composition root) decide si reconstruir clientes que dependan del cambio.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from ..config import Settings
from ..domain.errors import JobValidationError
from ..domain.ports import EnvWriter

_MIN_PARALLEL: Final[int] = 1
_MAX_PARALLEL: Final[int] = 16
_MIN_POLL_SECONDS: Final[int] = 1
_MAX_POLL_SECONDS: Final[int] = 600
_MIN_TIMEOUT_SECONDS: Final[int] = 60
_MAX_TIMEOUT_SECONDS: Final[int] = 86_400


@dataclass(frozen=True, slots=True)
class EditableSettings:
    """Vista plana de los settings editables desde la UI."""

    kie_api_base: str
    kie_upload_base: str
    max_parallel_jobs: int
    poll_interval_seconds: int
    task_timeout_seconds: int
    default_voice: str
    default_prompt: str


class SettingsController:
    """Lectura/escritura de los settings no-keys persistidos en `.env`."""

    def __init__(self, settings: Settings, env: EnvWriter) -> None:
        self._settings = settings
        self._env = env

    def snapshot(self) -> EditableSettings:
        """Devuelve el estado actual de los settings (lectura desde `Settings`)."""
        return EditableSettings(
            kie_api_base=self._settings.kie_api_base,
            kie_upload_base=self._settings.kie_upload_base,
            max_parallel_jobs=self._settings.max_parallel_jobs,
            poll_interval_seconds=self._settings.poll_interval_seconds,
            task_timeout_seconds=self._settings.task_timeout_seconds,
            default_voice=self._settings.default_voice,
            default_prompt=self._settings.default_prompt,
        )

    def update_endpoints(self, api_base: str, upload_base: str) -> None:
        _require_https_url(api_base, field="KIE_API_BASE")
        _require_https_url(upload_base, field="KIE_UPLOAD_BASE")
        self._env.set("KIE_API_BASE", api_base.strip())
        self._env.set("KIE_UPLOAD_BASE", upload_base.strip())

    def update_execution(
        self,
        max_parallel_jobs: int,
        poll_interval_seconds: int,
        task_timeout_seconds: int,
    ) -> None:
        _require_in_range(
            max_parallel_jobs, _MIN_PARALLEL, _MAX_PARALLEL, field="MAX_PARALLEL_JOBS"
        )
        _require_in_range(
            poll_interval_seconds,
            _MIN_POLL_SECONDS,
            _MAX_POLL_SECONDS,
            field="POLL_INTERVAL_SECONDS",
        )
        _require_in_range(
            task_timeout_seconds,
            _MIN_TIMEOUT_SECONDS,
            _MAX_TIMEOUT_SECONDS,
            field="TASK_TIMEOUT_SECONDS",
        )
        self._env.set("MAX_PARALLEL_JOBS", str(max_parallel_jobs))
        self._env.set("POLL_INTERVAL_SECONDS", str(poll_interval_seconds))
        self._env.set("TASK_TIMEOUT_SECONDS", str(task_timeout_seconds))

    def update_defaults(self, voice: str, prompt: str) -> None:
        if not voice.strip():
            raise JobValidationError("DEFAULT_VOICE no puede estar vacío")
        if not prompt.strip():
            raise JobValidationError("DEFAULT_PROMPT no puede estar vacío")
        self._env.set("DEFAULT_VOICE", voice.strip())
        self._env.set("DEFAULT_PROMPT", prompt.strip())


def _require_https_url(value: str, *, field: str) -> None:
    stripped = value.strip()
    if not (stripped.startswith("https://") or stripped.startswith("http://")):
        raise JobValidationError(f"{field} debe empezar con http:// o https://")


def _require_in_range(value: int, low: int, high: int, *, field: str) -> None:
    if not (low <= value <= high):
        raise JobValidationError(f"{field} debe estar entre {low} y {high}")
