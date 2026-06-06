"""Wrappers para invocar runners hoja respetando el limiter global de Kie.

El `WorkflowStepRunner` necesita invocar los runners de imagen/audio
DIRECTAMENTE (no a través de su `QueueManager` de UI) porque debe
esperar el resultado de forma sincrónica para el flujo del step. Pero
debe seguir respetando el límite global `max_parallel_jobs` para no
inundar Kie cuando un workflow tiene muchos sub-jobs corriendo en
paralelo.

`CapacityLimitedExecutor` resuelve este punto: wrappa un `RunnableRunner`
y un `asyncio.Semaphore` (compartido con las queues de UI), exponiendo
un único `run()` que adquiere el slot antes de delegar.

Es **distinto** del `QueueManager`:
- `QueueManager.enqueue(job)` es fire-and-forget con listeners (UI).
- `CapacityLimitedExecutor.run(job)` es awaitable (workflow orquesta).

Ambos pueden compartir el MISMO `Semaphore` global porque cada `run()`
ocupa exactamente un slot mientras corre y lo libera al terminar.
"""

from __future__ import annotations

import asyncio
from typing import Generic, TypeVar

from ..domain.ports import RunnableJob, RunnableRunner

T = TypeVar("T", bound=RunnableJob)


class CapacityLimitedExecutor(Generic[T]):
    """Wrappa un runner para que cada `run()` acquire el limiter global."""

    def __init__(self, inner: RunnableRunner[T], limiter: asyncio.Semaphore) -> None:
        self._inner = inner
        self._limiter = limiter

    async def run(self, job: T) -> T:
        async with self._limiter:
            return await self._inner.run(job)
