"""Persistencia de `GeneratedAudio` sobre la misma SQLite local que jobs (WAL).

Cumple `domain.ports.AudioStore`. Comparte archivo con `JobsDB` e `ImagesDB`
pero su propia tabla; cada operación abre/cierra conexión (SPEC §7.2).

`voice_settings` se persiste como JSON string nullable: nullable porque el
audio puede haber sido generado con los defaults del proveedor (sin enviar
settings), JSON porque la estructura puede crecer sin tener que migrar la
tabla (CR-3.7 evita columnas que se repiten por cada nuevo campo).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Final

import aiosqlite

from ..domain.errors import AudioNotFoundError
from ..domain.models import GeneratedAudio, VoiceSettings

_SCHEMA: Final[str] = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS generated_audios (
  id                   TEXT PRIMARY KEY,
  label                TEXT NOT NULL,
  script               TEXT NOT NULL,
  voice_id             TEXT NOT NULL,
  voice_settings_json  TEXT,
  kie_url              TEXT NOT NULL,
  kie_file_path        TEXT NOT NULL,
  file_size            INTEGER,
  mime_type            TEXT,
  duration_seconds     REAL,
  generated_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_generated_audios_generated_at
  ON generated_audios(generated_at);
"""

_UPSERT_SQL: Final[str] = """
INSERT INTO generated_audios(
  id, label, script, voice_id, voice_settings_json,
  kie_url, kie_file_path, file_size, mime_type, duration_seconds, generated_at
) VALUES (
  :id, :label, :script, :voice_id, :voice_settings_json,
  :kie_url, :kie_file_path, :file_size, :mime_type, :duration_seconds, :generated_at
)
ON CONFLICT(id) DO UPDATE SET
  label=excluded.label,
  script=excluded.script,
  voice_id=excluded.voice_id,
  voice_settings_json=excluded.voice_settings_json,
  kie_url=excluded.kie_url,
  kie_file_path=excluded.kie_file_path,
  file_size=excluded.file_size,
  mime_type=excluded.mime_type,
  duration_seconds=excluded.duration_seconds,
  generated_at=excluded.generated_at
"""


class AudiosDB:
    """Repositorio SQLite de audios TTS ya generados por Kie."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()

    async def list_recent(self, limit: int = 100) -> list[GeneratedAudio]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM generated_audios ORDER BY generated_at DESC LIMIT ?",
                (limit,),
            )
            rows = await cur.fetchall()
        return [self._row_to_audio(row) for row in rows]

    async def get(self, audio_id: str) -> GeneratedAudio | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM generated_audios WHERE id = ?", (audio_id,))
            row = await cur.fetchone()
        return self._row_to_audio(row) if row else None

    async def upsert(self, audio: GeneratedAudio) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(_UPSERT_SQL, self._audio_to_row(audio))
            await db.commit()

    async def delete(self, audio_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("DELETE FROM generated_audios WHERE id = ?", (audio_id,))
            await db.commit()
        if cur.rowcount == 0:
            raise AudioNotFoundError(f"no existe ningún audio con id={audio_id!r}")

    async def delete_many(self, audio_ids: list[str]) -> None:
        """Borra múltiples audios en una sola transacción.

        Útil para el cleanup periódico de expirados. No lanza si algún id no
        existe — la operación es idempotente, lo que importa es el estado final.
        """
        if not audio_ids:
            return
        placeholders = ",".join("?" * len(audio_ids))
        async with aiosqlite.connect(self.db_path) as db:
            # Los `?` se generan a partir de un literal interno (no input de
            # usuario) y los `audio_ids` van como parámetros bindeados, así
            # que no hay riesgo real de inyección. Falso positivo de ruff S608.
            await db.execute(
                f"DELETE FROM generated_audios WHERE id IN ({placeholders})",  # noqa: S608
                audio_ids,
            )
            await db.commit()

    # --- mappers -----------------------------------------------------------

    @staticmethod
    def _audio_to_row(audio: GeneratedAudio) -> dict[str, Any]:
        settings_json: str | None = None
        if audio.voice_settings is not None and not audio.voice_settings.is_empty():
            settings_json = audio.voice_settings.model_dump_json(exclude_none=True)
        return {
            "id": audio.id,
            "label": audio.label,
            "script": audio.script,
            "voice_id": audio.voice_id,
            "voice_settings_json": settings_json,
            "kie_url": audio.kie_url,
            "kie_file_path": audio.kie_file_path,
            "file_size": audio.file_size,
            "mime_type": audio.mime_type,
            "duration_seconds": audio.duration_seconds,
            "generated_at": audio.generated_at.isoformat(),
        }

    @staticmethod
    def _row_to_audio(row: aiosqlite.Row) -> GeneratedAudio:
        settings_json = row["voice_settings_json"]
        settings = VoiceSettings.model_validate_json(settings_json) if settings_json else None
        return GeneratedAudio(
            id=row["id"],
            label=row["label"],
            script=row["script"],
            voice_id=row["voice_id"],
            voice_settings=settings,
            kie_url=row["kie_url"],
            kie_file_path=row["kie_file_path"],
            file_size=row["file_size"],
            mime_type=row["mime_type"],
            duration_seconds=row["duration_seconds"],
            generated_at=datetime.fromisoformat(row["generated_at"]),
        )
