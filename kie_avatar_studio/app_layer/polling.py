"""Helpers compartidos para hacer polling sobre tasks de Kie hasta tener URL.

Tanto `JobRunner` como `AudiosController` necesitan esperar a que un task
(TTS o avatar) termine y devuelva la URL del resultado. Centralizar la
lógica acá evita duplicación (CR-3.7) y deja un único punto de mantenimiento
si Kie cambia el shape de `recordInfo`.
"""

from __future__ import annotations

import asyncio

from ..domain.errors import KieError, KieTimeoutError
from ..domain.policies import extract_failure_message, extract_result_url, extract_task_status
from ..domain.ports import KieGateway


async def poll_task_for_url(
    gateway: KieGateway,
    task_id: str,
    *,
    kind: str,
    interval_seconds: int,
    timeout_seconds: int,
) -> str:
    """Espera al task y devuelve la URL del resultado.

    `kind` es solo para mensajes de error legibles (ej. "audio", "video").
    `interval_seconds` se clamp-ea a un mínimo de 1 para evitar busy-loop si
    el caller pasa 0 o negativo.

    Lanza:
    - `KieTimeoutError` si pasan `timeout_seconds` sin éxito.
    - `KieError` si el task termina como `failed` o si llega a `success` sin
      una URL extraible del payload.
    """
    elapsed = 0
    interval = max(1, interval_seconds)
    while elapsed < timeout_seconds:
        detail = await gateway.get_task_detail(task_id)
        status = extract_task_status(detail)
        if status == "success":
            url = extract_result_url(detail)
            if url:
                return url
            raise KieError(f"{kind} task {task_id} terminado sin URL: {detail!r}")
        if status == "failed":
            reason = extract_failure_message(detail) or repr(detail)
            raise KieError(f"{kind} task {task_id} fallido: {reason}")
        await asyncio.sleep(interval)
        elapsed += interval
    raise KieTimeoutError(f"{kind} task {task_id} excedió {timeout_seconds}s")
