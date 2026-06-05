"""`BatchController`: orquesta el procesamiento de lotes desde `batch_jobs/`.

Capa de application: NO toca filesystem ni red directamente. Delega:
- `scan_loader`: callable async que devuelve `list[BatchEntry]` (lo cablea
  `app.py` apuntando a `infra.batch_loader.scan_batch_dir` con el path y
  defaults del `Settings`).
- `videos_controller.enqueue_from_scratch`: la creación real del VideoJob
  + persistencia + encolado.

Encapsula la lógica de "encolar todas las válidas" y "encolar una sola"
para que la UI no tenga que recorrer la lista ni manejar errores
parciales. Devuelve siempre un resumen estructurado en lugar de lanzar
en caso de errores individuales (un lote con 100 carpetas no debe
detenerse en la primera inválida).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from loguru import logger

from ..domain.errors import JobValidationError
from ..domain.models import BatchEntry, VideoJob
from .videos_controller import VideosController

BatchScanLoader = Callable[[], Awaitable[list[BatchEntry]]]


@dataclass(frozen=True, slots=True)
class BatchEnqueueResult:
    """Resumen de un encolado masivo del lote.

    `enqueued_ids` y `errors` son disjuntos: cada entry contribuye a uno
    u otro. `skipped_invalid` cuenta los que la UI mostraba como inválidos
    antes de invocar y no se intentaron encolar.
    """

    enqueued_ids: list[str]
    errors: list[tuple[str, str]]
    skipped_invalid: int

    @property
    def total_attempted(self) -> int:
        return len(self.enqueued_ids) + len(self.errors)


class BatchController:
    """Casos de uso del flujo `Procesar lote`.

    Mantiene un cache trivial de la última `scan` para que las acciones
    (encolar, etc.) operen sobre la lista que el usuario está viendo,
    no sobre el filesystem mutado entre tanto. La UI puede invalidarlo
    llamando `list_entries(refresh=True)`.
    """

    def __init__(
        self,
        *,
        scan_loader: BatchScanLoader,
        videos_controller: VideosController,
    ) -> None:
        self._scan_loader = scan_loader
        self._videos = videos_controller
        self._cached: list[BatchEntry] | None = None

    async def list_entries(self, *, refresh: bool = False) -> list[BatchEntry]:
        """Devuelve los lotes disponibles, opcionalmente refrescando del FS."""
        if refresh or self._cached is None:
            self._cached = await self._scan_loader()
        return list(self._cached)

    async def enqueue_entry(self, entry: BatchEntry) -> VideoJob:
        """Encola un `BatchEntry` válido. Lanza `JobValidationError` si no lo es."""
        if not entry.valid:
            raise JobValidationError(
                f"el lote '{entry.name}' no es válido: {'; '.join(entry.errors)}"
            )
        if entry.image_path is None:
            raise JobValidationError(f"el lote '{entry.name}' no tiene imagen resuelta")
        return await self._videos.enqueue_from_scratch(
            script=entry.script,
            image_path=str(entry.image_path),
            voice=entry.voice,
            prompt=entry.prompt,
        )

    async def enqueue_all_valid(self) -> BatchEnqueueResult:
        """Encola todas las entries válidas del último `list_entries`.

        Recolecta errores individuales en lugar de abortar al primero:
        un lote con 100 carpetas debe maximizar throughput aunque alguna
        falle (ej. imagen corrupta detectada por `validate_job`).
        """
        entries = await self.list_entries()
        enqueued: list[str] = []
        errors: list[tuple[str, str]] = []
        skipped = 0
        for entry in entries:
            if not entry.valid:
                skipped += 1
                continue
            try:
                job = await self.enqueue_entry(entry)
                enqueued.append(job.id)
            except Exception as exc:
                logger.opt(exception=True).warning(
                    "Lote '{}' no pudo encolarse: {}", entry.name, exc
                )
                errors.append((entry.name, str(exc)))
        return BatchEnqueueResult(
            enqueued_ids=enqueued,
            errors=errors,
            skipped_invalid=skipped,
        )
