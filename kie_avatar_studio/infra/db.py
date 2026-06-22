"""Persistencia SQLite (aiosqlite) para `VideoJob`. Implementa `JobRepository`.

Cada operación abre y cierra su conexión; WAL mode permite que lectores y
escritores convivan sin bloquearse, lo que es suficiente para la escala
local de esta app.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import aiosqlite

from ..domain.models import JobStatus, VideoJob

_SCHEMA: Final[str] = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  status TEXT NOT NULL,
  script TEXT NOT NULL,
  image_path TEXT NOT NULL,
  prompt TEXT NOT NULL,
  voice TEXT NOT NULL,
  image_url TEXT,
  audio_task_id TEXT,
  audio_url TEXT,
  video_task_id TEXT,
  video_url TEXT,
  output_path TEXT,
  error TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at);
"""

_UPSERT_SQL: Final[str] = """
INSERT INTO jobs(id, created_at, updated_at, status, script, image_path, prompt, voice,
                 image_url, audio_task_id, audio_url, video_task_id, video_url, output_path, error)
VALUES(:id, :created_at, :updated_at, :status, :script, :image_path, :prompt, :voice,
       :image_url, :audio_task_id, :audio_url, :video_task_id, :video_url, :output_path, :error)
ON CONFLICT(id) DO UPDATE SET
  updated_at=excluded.updated_at,
  status=excluded.status,
  script=excluded.script,
  image_path=excluded.image_path,
  prompt=excluded.prompt,
  voice=excluded.voice,
  image_url=excluded.image_url,
  audio_task_id=excluded.audio_task_id,
  audio_url=excluded.audio_url,
  video_task_id=excluded.video_task_id,
  video_url=excluded.video_url,
  output_path=excluded.output_path,
  error=excluded.error
"""


class JobsDB:
    """Repositorio de `VideoJob` sobre SQLite local."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(_SCHEMA)
            # 2026-06-21: `video_task_id` permite reanudar polling de video
            # manual tras restart sin recrear el task en Kie.
            try:
                await db.execute("ALTER TABLE jobs ADD COLUMN video_task_id TEXT")
            except aiosqlite.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
            await db.commit()

    async def upsert(self, job: VideoJob) -> None:
        job.updated_at = datetime.now(UTC)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(_UPSERT_SQL, self._job_to_row(job))
            await db.commit()

    async def get(self, job_id: str) -> VideoJob | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            row = await cur.fetchone()
        return self._row_to_job(row) if row else None

    async def list_recent(self, limit: int = 50) -> list[VideoJob]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))
            rows = await cur.fetchall()
        return [self._row_to_job(row) for row in rows]

    async def list_by_status(self, status: JobStatus) -> list[VideoJob]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY created_at ASC",
                (status.value,),
            )
            rows = await cur.fetchall()
        return [self._row_to_job(row) for row in rows]

    async def delete(self, job_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            await db.commit()

    # --- mappers ------------------------------------------------------------

    @staticmethod
    def _job_to_row(job: VideoJob) -> dict[str, Any]:
        return {
            "id": job.id,
            "created_at": job.created_at.isoformat(),
            "updated_at": job.updated_at.isoformat(),
            "status": job.status.value,
            "script": job.script,
            "image_path": job.image_path,
            "prompt": job.prompt,
            "voice": job.voice,
            "image_url": job.image_url,
            "audio_task_id": job.audio_task_id,
            "audio_url": job.audio_url,
            "video_task_id": job.video_task_id,
            "video_url": job.video_url,
            "output_path": job.output_path,
            "error": job.error,
        }

    @staticmethod
    def _row_to_job(row: aiosqlite.Row) -> VideoJob:
        return VideoJob(
            id=row["id"],
            script=row["script"],
            image_path=row["image_path"],
            prompt=row["prompt"],
            voice=row["voice"],
            status=JobStatus(row["status"]),
            image_url=row["image_url"],
            audio_task_id=row["audio_task_id"],
            audio_url=row["audio_url"],
            video_task_id=row["video_task_id"],
            video_url=row["video_url"],
            output_path=row["output_path"],
            error=row["error"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
