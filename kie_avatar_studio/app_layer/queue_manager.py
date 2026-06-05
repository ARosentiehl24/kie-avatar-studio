"""`QueueManager`: cola genérica con paralelismo limitado por semáforo (SPEC §10).

Refactor a `Generic[T, EventT]` para reusar el mismo manager con distintos
tipos de job (`VideoJob`, `AudioJob`):

- `T = TypeVar bound RunnableJob`: el tipo del job.
- `EventT`: el tipo del evento que se emite a los listeners (un dataclass
  típicamente).
- `event_factory: Callable[[T], EventT]`: cómo construir el evento desde el job.
- `lifecycle: JobLifecycle[T]`: reglas específicas de cancel/retry/persist.
- `capacity_limiter: asyncio.Semaphore | None`: si se pasa, lo usa en lugar
  de crear uno propio. Permite compartir un límite global entre múltiples
  QueueManagers (audio + video respetan el mismo `max_parallel_jobs`).

Soporta:
- `enqueue` para encolar y disparar.
- `cancel` para abortar un job activo (delega a `lifecycle.is_cancellable`).
- `retry` para reencolar un job en estado terminal fallido (delega a
  `lifecycle.is_retryable`).
- `restore_pending` para reanudar jobs no terminales al arrancar la app.
- Listeners sync o async que reciben cada evento.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any, Generic, TypeVar

from loguru import logger

from ..config import Settings
from ..domain.ports import JobLifecycle, RunnableJob, RunnableRunner

T = TypeVar("T", bound=RunnableJob)
EventT = TypeVar("EventT")

Listener = Callable[[Any], None] | Callable[[Any], Awaitable[None]]


class JobRepository(Generic[T]):
    """Mini-interface para `restore_pending` parametrizada por el status concreto.

    Definimos el shape mínimo que el queue necesita para restaurar jobs
    desde un repositorio persistente. Cada implementación concreta
    (JobsDB, AudioJobsDB) provee este método de forma natural.
    """

    async def list_resumable(self) -> list[T]:  # pragma: no cover - interface only
        raise NotImplementedError


ResumableLoader = Callable[[], Awaitable[list[T]]]
"""Factory async que devuelve los jobs reanudables al arrancar la app."""


class QueueManager(Generic[T, EventT]):
    """Coordina la ejecución concurrente de jobs respetando un límite."""

    def __init__(
        self,
        settings: Settings,
        runner: RunnableRunner[T],
        *,
        event_factory: Callable[[T], EventT],
        lifecycle: JobLifecycle[T],
        capacity_limiter: asyncio.Semaphore | None = None,
    ) -> None:
        self._settings = settings
        self._runner = runner
        self._event_factory = event_factory
        self._lifecycle = lifecycle
        # Si recibimos un semáforo compartido, lo usamos; si no, creamos uno
        # local. El primer caso es el que respeta el límite global cuando
        # hay múltiples QueueManagers en la app.
        self._semaphore = capacity_limiter or asyncio.Semaphore(max(1, settings.max_parallel_jobs))
        self._pending: deque[T] = deque()
        self._active: dict[str, asyncio.Task[None]] = {}
        self._jobs_by_id: dict[str, T] = {}
        self._listeners: list[Listener] = []
        # Mantener referencias fuertes a las tareas "fire-and-forget" de listeners
        # async para evitar que el GC las recoja antes de terminar (RUF006).
        self._listener_tasks: set[asyncio.Task[None]] = set()

    # --- API pública -------------------------------------------------------

    def add_listener(self, callback: Listener) -> Callable[[], None]:
        """Registra un listener y devuelve un callable para desuscribir.

        Las pantallas Textual deben llamar al unsubscribe en `on_unmount`
        para evitar dejar listeners colgados que intentan actualizar
        widgets ya desmontados.
        """
        self._listeners.append(callback)

        def unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._listeners.remove(callback)

        return unsubscribe

    def enqueue(self, job: T) -> None:
        self._jobs_by_id[job.id] = job
        self._pending.append(job)
        self._notify(job)
        self._maybe_dispatch()

    async def cancel(self, job_id: str) -> bool:
        """Cancela un job activo o lo retira de la cola.

        Persiste el cambio de estado (write-ahead) antes de mutar memoria,
        de modo que un restart después de cancelar no resucita el job.
        Devuelve True si cambió algo.
        """
        job = self._jobs_by_id.get(job_id)
        if job is None or not self._lifecycle.is_cancellable(job):
            return False
        task = self._active.get(job_id)
        if task is not None and not task.done():
            task.cancel()
        else:
            with contextlib.suppress(ValueError):
                self._pending.remove(job)
        await self._lifecycle.mark_cancelled(job)
        self._notify(job)
        return True

    async def retry(self, job: T) -> bool:
        """Reencola un job en estado terminal fallido/cancelado.

        Persiste el reset (status=queued, error=None) antes de re-enqueue.
        """
        if not self._lifecycle.is_retryable(job):
            return False
        await self._lifecycle.reset_for_retry(job)
        self.enqueue(job)
        return True

    async def restore_pending(self, loader: ResumableLoader[T]) -> int:
        """Carga jobs en estados reanudables y los reencola. Devuelve cuántos.

        `loader` es una función async que devuelve la lista de jobs a
        restaurar. Permite que el queue no conozca el repositorio
        concreto (DIP) — el composition root provee la closure.
        """
        recovered = 0
        for job in await loader():
            self.enqueue(job)
            recovered += 1
        return recovered

    async def drain(self) -> None:
        if self._active:
            await asyncio.gather(*self._active.values(), return_exceptions=True)

    # --- ciclo interno -----------------------------------------------------

    def _maybe_dispatch(self) -> None:
        while self._pending and len(self._active) < self._settings.max_parallel_jobs:
            job = self._pending.popleft()
            task = asyncio.create_task(self._run(job), name=f"job-{job.id}")
            self._active[job.id] = task
            task.add_done_callback(self._build_done_callback(job.id))

    def _build_done_callback(self, job_id: str) -> Callable[[asyncio.Task[None]], None]:
        def _on_done(_task: asyncio.Task[None]) -> None:
            self._active.pop(job_id, None)
            self._maybe_dispatch()

        return _on_done

    async def _run(self, job: T) -> None:
        async with self._semaphore:
            try:
                result = await self._runner.run(job)
            except asyncio.CancelledError:
                logger.info("Job {} cancelado", job.id)
                raise
            self._notify(result)

    def _notify(self, job: T) -> None:
        event = self._event_factory(job)
        for callback in self._listeners:
            self._dispatch_listener(callback, event)

    def _dispatch_listener(self, callback: Listener, event: EventT) -> None:
        try:
            result = callback(event)
        except Exception:
            logger.exception("listener síncrono falló")
            return
        if inspect.isawaitable(result):
            task = asyncio.create_task(_await_listener(result))
            self._listener_tasks.add(task)
            task.add_done_callback(self._listener_tasks.discard)


async def _await_listener(awaitable: Awaitable[None]) -> None:
    try:
        await awaitable
    except Exception:
        logger.exception("listener asíncrono falló")
