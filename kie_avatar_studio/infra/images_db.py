"""Persistencia de `UploadedImage` sobre la misma SQLite local que jobs (WAL).

Cumple `domain.ports.ImageStore`. Comparte archivo con `JobsDB` pero su propia
tabla; cada operación abre/cierra conexión (SPEC §7.2).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Final

import aiosqlite

from ..domain.errors import ImageNotFoundError
from ..domain.models import UploadedImage

_SCHEMA: Final[str] = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS uploaded_images (
  id            TEXT PRIMARY KEY,
  label         TEXT NOT NULL,
  local_path    TEXT NOT NULL,
  kie_url       TEXT NOT NULL,
  kie_file_path TEXT NOT NULL,
  file_size     INTEGER NOT NULL,
  mime_type     TEXT NOT NULL,
  uploaded_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_uploaded_images_uploaded_at
  ON uploaded_images(uploaded_at);
"""

_UPSERT_SQL: Final[str] = """
INSERT INTO uploaded_images(
  id, label, local_path, kie_url, kie_file_path, file_size, mime_type, uploaded_at
) VALUES (
  :id, :label, :local_path, :kie_url, :kie_file_path, :file_size, :mime_type, :uploaded_at
)
ON CONFLICT(id) DO UPDATE SET
  label=excluded.label,
  local_path=excluded.local_path,
  kie_url=excluded.kie_url,
  kie_file_path=excluded.kie_file_path,
  file_size=excluded.file_size,
  mime_type=excluded.mime_type,
  uploaded_at=excluded.uploaded_at
"""


class ImagesDB:
    """Repositorio SQLite de imágenes ya subidas a Kie."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()

    async def list_recent(self, limit: int = 100) -> list[UploadedImage]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM uploaded_images ORDER BY uploaded_at DESC LIMIT ?",
                (limit,),
            )
            rows = await cur.fetchall()
        return [self._row_to_image(row) for row in rows]

    async def get(self, image_id: str) -> UploadedImage | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM uploaded_images WHERE id = ?", (image_id,))
            row = await cur.fetchone()
        return self._row_to_image(row) if row else None

    async def upsert(self, image: UploadedImage) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(_UPSERT_SQL, self._image_to_row(image))
            await db.commit()

    async def delete(self, image_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("DELETE FROM uploaded_images WHERE id = ?", (image_id,))
            await db.commit()
        if cur.rowcount == 0:
            raise ImageNotFoundError(f"no existe ninguna imagen con id={image_id!r}")

    # --- mappers -----------------------------------------------------------

    @staticmethod
    def _image_to_row(image: UploadedImage) -> dict[str, Any]:
        return {
            "id": image.id,
            "label": image.label,
            "local_path": image.local_path,
            "kie_url": image.kie_url,
            "kie_file_path": image.kie_file_path,
            "file_size": image.file_size,
            "mime_type": image.mime_type,
            "uploaded_at": image.uploaded_at.isoformat(),
        }

    @staticmethod
    def _row_to_image(row: aiosqlite.Row) -> UploadedImage:
        return UploadedImage(
            id=row["id"],
            label=row["label"],
            local_path=row["local_path"],
            kie_url=row["kie_url"],
            kie_file_path=row["kie_file_path"],
            file_size=row["file_size"],
            mime_type=row["mime_type"],
            uploaded_at=datetime.fromisoformat(row["uploaded_at"]),
        )
