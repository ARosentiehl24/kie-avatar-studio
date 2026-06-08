"""Persistencia de `WorkflowJob` + `WorkflowStep` sobre SQLite local (WAL).

Cumple `domain.ports.WorkflowRepository`. Comparte archivo (`data/jobs.db`)
con los demás stores, pero usa dos tablas propias:

- `workflow_jobs`: header del workflow (id, name, status, pre_settings_json,
  output_dir, etc.).
- `workflow_steps`: una fila por step, FK al workflow. `progress_json`
  guarda el dict de `WorkflowProgressKey -> WorkflowProgressStatus`.

A diferencia de los otros repos (que persisten todo el job en cada upsert),
`WorkflowRepository` ofrece updates granulares:
- `upsert_workflow`: header + lista completa de steps (en enqueue / restore).
- `update_workflow_header`: solo el header del workflow (status, error, etc.).
- `upsert_step`: una sola fila de `workflow_steps` (en cada transición del
  step runner, evita lost updates entre steps corriendo en paralelo).

Pattern de cada operación: conexión por operación (CR-3.7) con WAL mode.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import aiosqlite

from ..domain.models import (
    WorkflowJob,
    WorkflowPreSettings,
    WorkflowProgressKey,
    WorkflowProgressStatus,
    WorkflowStatus,
    WorkflowStep,
    WorkflowStepStatus,
)

_SCHEMA: Final[str] = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS workflow_jobs (
  id                     TEXT PRIMARY KEY,
  created_at             TEXT NOT NULL,
  updated_at             TEXT NOT NULL,
  status                 TEXT NOT NULL,
  name                   TEXT NOT NULL,
  slug                   TEXT NOT NULL,
  source_json_path       TEXT NOT NULL,
  output_dir             TEXT NOT NULL,
  pre_settings_json      TEXT NOT NULL,
  error                  TEXT,
  manifest_write_failed  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_workflow_jobs_status ON workflow_jobs(status);
CREATE INDEX IF NOT EXISTS idx_workflow_jobs_created_at ON workflow_jobs(created_at);

CREATE TABLE IF NOT EXISTS workflow_steps (
  workflow_id            TEXT NOT NULL,
  step                   INTEGER NOT NULL,
  scene_name             TEXT NOT NULL,
  scene_slug             TEXT NOT NULL,
  type                   TEXT NOT NULL,
  change_scene           INTEGER NOT NULL DEFAULT 0,
  scene_description      TEXT NOT NULL DEFAULT '',
  prompt                 TEXT NOT NULL,
  text                   TEXT NOT NULL DEFAULT '',
  duration_seconds       INTEGER,
  voiceover              INTEGER NOT NULL DEFAULT 1,
  include_product        INTEGER NOT NULL DEFAULT 0,
  include_model          INTEGER NOT NULL DEFAULT 1,
  product_prompt         TEXT NOT NULL DEFAULT '',
  scene_image_approved_at TEXT,
  image_aspect_ratio     TEXT,
  bg_image_job_id        TEXT,
  audio_job_id           TEXT,
  video_task_id          TEXT,
  scene_image_path       TEXT,
  audio_path             TEXT,
  video_path             TEXT,
  status                 TEXT NOT NULL,
  progress_json          TEXT NOT NULL DEFAULT '{}',
  error                  TEXT,
  started_at             TEXT,
  completed_at           TEXT,
  PRIMARY KEY (workflow_id, step),
  FOREIGN KEY (workflow_id) REFERENCES workflow_jobs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_workflow_steps_status ON workflow_steps(status);
"""

_UPSERT_WORKFLOW_SQL: Final[str] = """
INSERT INTO workflow_jobs(
  id, created_at, updated_at, status, name, slug,
  source_json_path, output_dir, pre_settings_json, error, manifest_write_failed
) VALUES (
  :id, :created_at, :updated_at, :status, :name, :slug,
  :source_json_path, :output_dir, :pre_settings_json, :error, :manifest_write_failed
)
ON CONFLICT(id) DO UPDATE SET
  updated_at=excluded.updated_at,
  status=excluded.status,
  name=excluded.name,
  slug=excluded.slug,
  source_json_path=excluded.source_json_path,
  output_dir=excluded.output_dir,
  pre_settings_json=excluded.pre_settings_json,
  error=excluded.error,
  manifest_write_failed=excluded.manifest_write_failed
"""

_UPDATE_HEADER_SQL: Final[str] = """
UPDATE workflow_jobs SET
  updated_at = :updated_at,
  status = :status,
  error = :error,
  manifest_write_failed = :manifest_write_failed
WHERE id = :id
"""

_UPSERT_STEP_SQL: Final[str] = """
INSERT INTO workflow_steps(
  workflow_id, step, scene_name, scene_slug, type, change_scene,
  scene_description, prompt, text, duration_seconds, voiceover, bg_image_job_id,
  audio_job_id, video_task_id, scene_image_path, audio_path, video_path,
  scene_image_approved_at, include_product, include_model, product_prompt, image_aspect_ratio, status,
  progress_json, error, started_at, completed_at
) VALUES (
  :workflow_id, :step, :scene_name, :scene_slug, :type, :change_scene,
  :scene_description, :prompt, :text, :duration_seconds, :voiceover, :bg_image_job_id,
  :audio_job_id, :video_task_id, :scene_image_path, :audio_path, :video_path,
  :scene_image_approved_at, :include_product, :include_model, :product_prompt, :image_aspect_ratio, :status,
  :progress_json, :error, :started_at, :completed_at
)
ON CONFLICT(workflow_id, step) DO UPDATE SET
  scene_name=excluded.scene_name,
  scene_slug=excluded.scene_slug,
  type=excluded.type,
  change_scene=excluded.change_scene,
  scene_description=excluded.scene_description,
  prompt=excluded.prompt,
  text=excluded.text,
  duration_seconds=excluded.duration_seconds,
  voiceover=excluded.voiceover,
  bg_image_job_id=excluded.bg_image_job_id,
  audio_job_id=excluded.audio_job_id,
  video_task_id=excluded.video_task_id,
  scene_image_path=excluded.scene_image_path,
  audio_path=excluded.audio_path,
  video_path=excluded.video_path,
  scene_image_approved_at=excluded.scene_image_approved_at,
  include_product=excluded.include_product,
  include_model=excluded.include_model,
  product_prompt=excluded.product_prompt,
  image_aspect_ratio=excluded.image_aspect_ratio,
  status=excluded.status,
  progress_json=excluded.progress_json,
  error=excluded.error,
  started_at=excluded.started_at,
  completed_at=excluded.completed_at
"""


class WorkflowDB:
    """Repositorio de `WorkflowJob` + `WorkflowStep` sobre SQLite local."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(_SCHEMA)
            # Migraciones defensivas. Mismo patrón en todas: try ALTER, swallow
            # "duplicate column" o "no such column" según corresponda
            # (idempotente — la migración ya corrió). SQLite no soporta
            # `ADD COLUMN IF NOT EXISTS` ni `RENAME COLUMN IF EXISTS` con
            # sintaxis estándar, así que esto es lo más portable.
            #
            # 2026-06-06: `duration_seconds` (b-roll i2v override por step).
            # 2026-06-06: `voiceover` (b-roll: true=TTS aparte, false=Kling
            #             sound efx nativos embebidos en el video).
            # 2026-06-06: `scene_image_approved_at` (timestamp de aprobación
            #             humana cuando scene_approval_mode=manual).
            # 2026-06-07: `include_product` + `product_prompt` (producto
            #             promocional compuesto sobre la base con Nano Banana).
            # 2026-06-06: rename `change_background → change_scene` y
            #             `background_description → scene_description`
            #             (mejor semántica: el flag dispara regenerar TODA
            #             la scene image con Nano Banana, no solo el fondo).
            for column_ddl in (
                "ALTER TABLE workflow_steps ADD COLUMN duration_seconds INTEGER",
                "ALTER TABLE workflow_steps ADD COLUMN voiceover INTEGER NOT NULL DEFAULT 1",
                "ALTER TABLE workflow_steps ADD COLUMN scene_image_approved_at TEXT",
                "ALTER TABLE workflow_steps ADD COLUMN include_product INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE workflow_steps ADD COLUMN include_model INTEGER NOT NULL DEFAULT 1",
                "ALTER TABLE workflow_steps ADD COLUMN product_prompt TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE workflow_steps ADD COLUMN image_aspect_ratio TEXT",
            ):
                try:
                    await db.execute(column_ddl)
                except aiosqlite.OperationalError as exc:
                    if "duplicate column" not in str(exc).lower():
                        raise
            for rename_ddl in (
                "ALTER TABLE workflow_steps RENAME COLUMN change_background TO change_scene",
                "ALTER TABLE workflow_steps RENAME COLUMN background_description TO scene_description",
            ):
                try:
                    await db.execute(rename_ddl)
                except aiosqlite.OperationalError as exc:
                    msg = str(exc).lower()
                    # "no such column" → la columna vieja no existe; "duplicate" →
                    # el rename ya pasó. Ambos casos = ya migrado, no-op.
                    if "no such column" not in msg and "duplicate" not in msg:
                        raise
            await db.commit()

    async def upsert_workflow(self, workflow: WorkflowJob) -> None:
        """Persiste el header + TODOS los steps. Usar en enqueue inicial."""
        workflow.updated_at = datetime.now(UTC)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(_UPSERT_WORKFLOW_SQL, self._workflow_to_row(workflow))
            for step in workflow.steps:
                await db.execute(_UPSERT_STEP_SQL, self._step_to_row(workflow.id, step))
            await db.commit()

    async def update_workflow_header(self, workflow: WorkflowJob) -> None:
        """Solo actualiza el header (status, error, manifest_write_failed)."""
        workflow.updated_at = datetime.now(UTC)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                _UPDATE_HEADER_SQL,
                {
                    "id": workflow.id,
                    "updated_at": workflow.updated_at.isoformat(),
                    "status": workflow.status.value,
                    "error": workflow.error,
                    "manifest_write_failed": int(workflow.manifest_write_failed),
                },
            )
            await db.commit()

    async def upsert_step(self, workflow_id: str, step: WorkflowStep) -> None:
        """Persiste UNA fila de `workflow_steps`. Llamado por cada transición."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(_UPSERT_STEP_SQL, self._step_to_row(workflow_id, step))
            await db.commit()

    async def get(self, workflow_id: str) -> WorkflowJob | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM workflow_jobs WHERE id = ?", (workflow_id,))
            row = await cur.fetchone()
            if row is None:
                return None
            steps_cur = await db.execute(
                "SELECT * FROM workflow_steps WHERE workflow_id = ? ORDER BY step",
                (workflow_id,),
            )
            step_rows = list(await steps_cur.fetchall())
        return self._row_to_workflow(row, step_rows)

    async def list_recent(self, limit: int = 50) -> list[WorkflowJob]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM workflow_jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            rows = list(await cur.fetchall())
            return await self._load_workflows_with_steps(db, rows)

    async def list_by_status(self, status: WorkflowStatus) -> list[WorkflowJob]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM workflow_jobs WHERE status = ? ORDER BY created_at ASC",
                (status.value,),
            )
            rows = list(await cur.fetchall())
            return await self._load_workflows_with_steps(db, rows)

    async def delete(self, workflow_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            # ON DELETE CASCADE en workflow_steps; pero algunos pragmas no lo aplican.
            # Borramos ambas tablas explícitamente para garantizar limpieza.
            await db.execute("DELETE FROM workflow_steps WHERE workflow_id = ?", (workflow_id,))
            await db.execute("DELETE FROM workflow_jobs WHERE id = ?", (workflow_id,))
            await db.commit()

    # --- helpers internos --------------------------------------------------

    async def _load_workflows_with_steps(
        self, db: aiosqlite.Connection, rows: list[aiosqlite.Row]
    ) -> list[WorkflowJob]:
        workflows: list[WorkflowJob] = []
        for row in rows:
            steps_cur = await db.execute(
                "SELECT * FROM workflow_steps WHERE workflow_id = ? ORDER BY step",
                (row["id"],),
            )
            step_rows = list(await steps_cur.fetchall())
            workflows.append(self._row_to_workflow(row, step_rows))
        return workflows

    # --- mappers -----------------------------------------------------------

    @staticmethod
    def _workflow_to_row(workflow: WorkflowJob) -> dict[str, Any]:
        return {
            "id": workflow.id,
            "created_at": workflow.created_at.isoformat(),
            "updated_at": workflow.updated_at.isoformat(),
            "status": workflow.status.value,
            "name": workflow.name,
            "slug": workflow.slug,
            "source_json_path": workflow.source_json_path,
            "output_dir": workflow.output_dir,
            "pre_settings_json": workflow.pre_settings.model_dump_json(by_alias=True),
            "error": workflow.error,
            "manifest_write_failed": int(workflow.manifest_write_failed),
        }

    @staticmethod
    def _step_to_row(workflow_id: str, step: WorkflowStep) -> dict[str, Any]:
        return {
            "workflow_id": workflow_id,
            "step": step.step,
            "scene_name": step.scene_name,
            "scene_slug": step.scene_slug,
            "type": step.type.value,
            "change_scene": int(step.change_scene),
            "scene_description": step.scene_description,
            "prompt": step.prompt,
            "text": step.text,
            "duration_seconds": step.duration_seconds,
            "voiceover": int(step.voiceover),
            "include_product": int(step.include_product),
            "include_model": int(step.include_model),
            "product_prompt": step.product_prompt,
            "image_aspect_ratio": step.image_aspect_ratio,
            "bg_image_job_id": step.bg_image_job_id,
            "audio_job_id": step.audio_job_id,
            "video_task_id": step.video_task_id,
            "scene_image_path": step.scene_image_path,
            "audio_path": step.audio_path,
            "video_path": step.video_path,
            "scene_image_approved_at": (
                step.scene_image_approved_at.isoformat() if step.scene_image_approved_at else None
            ),
            "status": step.status.value,
            "progress_json": json.dumps(
                {k.value: v.value for k, v in step.progress.items()},
                ensure_ascii=False,
            ),
            "error": step.error,
            "started_at": step.started_at.isoformat() if step.started_at else None,
            "completed_at": step.completed_at.isoformat() if step.completed_at else None,
        }

    @staticmethod
    def _row_to_workflow(row: aiosqlite.Row, step_rows: list[aiosqlite.Row]) -> WorkflowJob:
        return WorkflowJob(
            id=row["id"],
            name=row["name"],
            slug=row["slug"],
            source_json_path=row["source_json_path"],
            output_dir=row["output_dir"],
            pre_settings=WorkflowPreSettings.model_validate_json(row["pre_settings_json"]),
            steps=[WorkflowDB._row_to_step(sr) for sr in step_rows],
            status=WorkflowStatus(row["status"]),
            error=row["error"],
            manifest_write_failed=bool(row["manifest_write_failed"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _row_to_step(row: aiosqlite.Row) -> WorkflowStep:
        raw_progress = json.loads(row["progress_json"] or "{}")
        progress = {
            WorkflowProgressKey(k): WorkflowProgressStatus(v) for k, v in raw_progress.items()
        }
        return WorkflowStep(
            step=row["step"],
            scene_name=row["scene_name"],
            scene_slug=row["scene_slug"],
            type=row["type"],
            change_scene=bool(row["change_scene"]),
            scene_description=row["scene_description"],
            prompt=row["prompt"],
            text=row["text"],
            duration_seconds=row["duration_seconds"],
            voiceover=bool(row["voiceover"]),
            include_product=bool(row["include_product"]),
            include_model=bool(row["include_model"]),
            product_prompt=row["product_prompt"],
            image_aspect_ratio=row["image_aspect_ratio"],
            bg_image_job_id=row["bg_image_job_id"],
            audio_job_id=row["audio_job_id"],
            video_task_id=row["video_task_id"],
            scene_image_path=row["scene_image_path"],
            audio_path=row["audio_path"],
            video_path=row["video_path"],
            scene_image_approved_at=(
                datetime.fromisoformat(row["scene_image_approved_at"])
                if row["scene_image_approved_at"]
                else None
            ),
            status=WorkflowStepStatus(row["status"]),
            progress=progress,
            error=row["error"],
            started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
            completed_at=datetime.fromisoformat(row["completed_at"])
            if row["completed_at"]
            else None,
        )
