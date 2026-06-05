"""Persistencia de `AudioJob` sobre la misma SQLite local (WAL).

Cumple `domain.ports.AudioJobRepository`. Comparte archivo con `JobsDB`,
`ImagesDB` y `AudiosDB` pero su propia tabla; cada operación abre/cierra
conexión (SPEC §7.2). El patrón es idéntico a `JobsDB`, solo cambia el
modelo.

`voice_settings_json` se persiste como string para no tener que migrar
la tabla si crecen los campos de `VoiceSettings` (CR-3.7). El parseo
hacia/desde el modelo lo hace `AudioJobRunner` al armar el request a
Kie y al construir el `GeneratedAudio` final.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import aiosqlite

from ..domain.models import AudioJob, AudioJobStatus

_SCHEMA: Final[str] = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS audio_jobs (
  id                   TEXT PRIMARY KEY,
  created_at           TEXT NOT NULL,
  updated_at           TEXT NOT NULL,
  status               TEXT NOT NULL,
  label                TEXT NOT NULL,
  script               TEXT NOT NULL,
  voice_id             TEXT NOT NULL,
  voice_settings_json  TEXT,
  task_id              TEXT,
  kie_url              TEXT,
  kie_file_path        TEXT,
  error                TEXT
);
CREATE INDEX IF NOT EXISTS idx_audio_jobs_status ON audio_jobs(status);
CREATE INDEX IF NOT EXISTS idx_audio_jobs_created_at ON audio_jobs(created_at);
"""

_UPSERT_SQL: Final[str] = """
INSERT INTO audio_jobs(
  id, created_at, updated_at, status, label, script, voice_id,
  voice_settings_json, task_id, kie_url, kie_file_path, error
) VALUES (
  :id, :created_at, :updated_at, :status, :label, :script, :voice_id,
  :voice_settings_json, :task_id, :kie_url, :kie_file_path, :error
)
ON CONFLICT(id) DO UPDATE SET
  updated_at=excluded.updated_at,
  status=excluded.status,
  label=excluded.label,
  script=excluded.script,
  voice_id=excluded.voice_id,
  voice_settings_json=excluded.voice_settings_json,
  task_id=excluded.task_id,
  kie_url=excluded.kie_url,
  kie_file_path=excluded.kie_file_path,
  error=excluded.error
"""


class AudioJobsDB:
    """Repositorio de `AudioJob` sobre SQLite local. Espejo de `JobsDB`."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()

    async def upsert(self, job: AudioJob) -> None:
        job.updated_at = datetime.now(UTC)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(_UPSERT_SQL, self._job_to_row(job))
            await db.commit()

    async def get(self, job_id: str) -> AudioJob | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM audio_jobs WHERE id = ?", (job_id,))
            row = await cur.fetchone()
        return self._row_to_job(row) if row else None

    async def list_recent(self, limit: int = 50) -> list[AudioJob]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM audio_jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            rows = await cur.fetchall()
        return [self._row_to_job(row) for row in rows]

    async def list_by_status(self, status: AudioJobStatus) -> list[AudioJob]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM audio_jobs WHERE status = ? ORDER BY created_at ASC",
                (status.value,),
            )
            rows = await cur.fetchall()
        return [self._row_to_job(row) for row in rows]

    async def delete(self, job_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM audio_jobs WHERE id = ?", (job_id,))
            await db.commit()

    # --- mappers ------------------------------------------------------------

    @staticmethod
    def _job_to_row(job: AudioJob) -> dict[str, Any]:
        return {
            "id": job.id,
            "created_at": job.created_at.isoformat(),
            "updated_at": job.updated_at.isoformat(),
            "status": job.status.value,
            "label": job.label,
            "script": job.script,
            "voice_id": job.voice_id,
            "voice_settings_json": job.voice_settings_json,
            "task_id": job.task_id,
            "kie_url": job.kie_url,
            "kie_file_path": job.kie_file_path,
            "error": job.error,
        }

    @staticmethod
    def _row_to_job(row: aiosqlite.Row) -> AudioJob:
        return AudioJob(
            id=row["id"],
            label=row["label"],
            script=row["script"],
            voice_id=row["voice_id"],
            voice_settings_json=row["voice_settings_json"],
            status=AudioJobStatus(row["status"]),
            task_id=row["task_id"],
            kie_url=row["kie_url"],
            kie_file_path=row["kie_file_path"],
            error=row["error"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
