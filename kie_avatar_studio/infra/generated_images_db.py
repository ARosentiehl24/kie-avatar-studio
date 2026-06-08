"""Persistencia de `GeneratedImage` sobre la misma SQLite local (WAL).

Cumple `domain.ports.GeneratedImageStore`. Espejo de `AudiosDB`: comparte
archivo con `JobsDB`, `ImagesDB`, `AudiosDB`, `AudioJobsDB` e `ImageJobsDB`,
pero su propia tabla; cada operación abre/cierra conexión (SPEC §7.2).

`settings_json` se persiste como JSON string nullable: nullable porque la
imagen pudo haberse generado con los defaults del modelo (sin enviar
settings explícitos), JSON porque la estructura puede crecer sin tener
que migrar la tabla (CR-3.7).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Final

import aiosqlite

from ..domain.errors import GeneratedImageNotFoundError
from ..domain.models import GeneratedImage, ImageGenerationSettings

_SCHEMA: Final[str] = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS generated_images (
  id              TEXT PRIMARY KEY,
  label           TEXT NOT NULL,
  prompt          TEXT NOT NULL,
  settings_json   TEXT,
  refs_count      INTEGER NOT NULL DEFAULT 0,
  kie_url         TEXT NOT NULL,
  kie_file_path   TEXT NOT NULL,
  file_size       INTEGER,
  mime_type       TEXT,
  generated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_generated_images_generated_at
  ON generated_images(generated_at);
"""

_UPSERT_SQL: Final[str] = """
INSERT INTO generated_images(
  id, label, prompt, settings_json, refs_count,
  kie_url, kie_file_path, file_size, mime_type, generated_at
) VALUES (
  :id, :label, :prompt, :settings_json, :refs_count,
  :kie_url, :kie_file_path, :file_size, :mime_type, :generated_at
)
ON CONFLICT(id) DO UPDATE SET
  label=excluded.label,
  prompt=excluded.prompt,
  settings_json=excluded.settings_json,
  refs_count=excluded.refs_count,
  kie_url=excluded.kie_url,
  kie_file_path=excluded.kie_file_path,
  file_size=excluded.file_size,
  mime_type=excluded.mime_type,
  generated_at=excluded.generated_at
"""


class GeneratedImagesDB:
    """Repositorio SQLite de imágenes ya generadas por Nano Banana 2."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()

    async def list_recent(self, limit: int = 100) -> list[GeneratedImage]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM generated_images ORDER BY generated_at DESC LIMIT ?",
                (limit,),
            )
            rows = await cur.fetchall()
        return [self._row_to_image(row) for row in rows]

    async def get(self, image_id: str) -> GeneratedImage | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM generated_images WHERE id = ?", (image_id,))
            row = await cur.fetchone()
        return self._row_to_image(row) if row else None

    async def upsert(self, image: GeneratedImage) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(_UPSERT_SQL, self._image_to_row(image))
            await db.commit()

    async def delete(self, image_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("DELETE FROM generated_images WHERE id = ?", (image_id,))
            await db.commit()
        if cur.rowcount == 0:
            raise GeneratedImageNotFoundError(
                f"no existe ninguna imagen generada con id={image_id!r}"
            )

    async def delete_many(self, image_ids: list[str]) -> None:
        """Borra múltiples imágenes en una sola transacción.

        Útil para el cleanup periódico de expiradas. Idempotente: no lanza
        si algún id no existe — importa el estado final.
        """
        if not image_ids:
            return
        placeholders = ",".join("?" * len(image_ids))
        async with aiosqlite.connect(self.db_path) as db:
            # Los `?` se generan a partir de un literal interno (no input
            # de usuario) y los `image_ids` van como parámetros bindeados.
            # Falso positivo de ruff S608.
            await db.execute(
                f"DELETE FROM generated_images WHERE id IN ({placeholders})",  # noqa: S608
                image_ids,
            )
            await db.commit()

    # --- mappers -----------------------------------------------------------

    @staticmethod
    def _image_to_row(image: GeneratedImage) -> dict[str, Any]:
        settings_json: str | None = None
        if image.settings is not None:
            settings_json = image.settings.model_dump_json(exclude_none=True)
        return {
            "id": image.id,
            "label": image.label,
            "prompt": image.prompt,
            "settings_json": settings_json,
            "refs_count": image.refs_count,
            "kie_url": image.kie_url,
            "kie_file_path": image.kie_file_path,
            "file_size": image.file_size,
            "mime_type": image.mime_type,
            "generated_at": image.generated_at.isoformat(),
        }

    @staticmethod
    def _row_to_image(row: aiosqlite.Row) -> GeneratedImage:
        settings_json = row["settings_json"]
        settings = (
            ImageGenerationSettings.model_validate_json(settings_json) if settings_json else None
        )
        return GeneratedImage(
            id=row["id"],
            label=row["label"],
            prompt=row["prompt"],
            settings=settings,
            refs_count=row["refs_count"],
            kie_url=row["kie_url"],
            kie_file_path=row["kie_file_path"],
            file_size=row["file_size"],
            mime_type=row["mime_type"],
            generated_at=datetime.fromisoformat(row["generated_at"]),
        )
