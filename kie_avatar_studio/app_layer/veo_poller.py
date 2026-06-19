"""Polling de tasks VEO 3.1 hasta obtener resultURLs.

VEO usa endpoints propios (/api/v1/veo/record-info) con un shape de
response distinto al genérico de /jobs/recordInfo. En particular:

- `successFlag` es int (0=generando, 1=success, 2=failed, 3=upstream failed),
  en vez del string `state` normalizado del helper genérico.
- Las URLs del resultado vienen en `data.response.resultUrls[]` (array),
  no en `data.recordInfo.url` (string).
- Los result URLs expiran a los 14 días — la app debe descargar antes.

Este módulo centraliza la lógica de polling + extracción de URLs para VEO,
de la misma forma que `polling.poll_task_for_url` lo hace para el wrapper
genérico de /jobs/.
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from ..domain.errors import KieError, KieTimeoutError
from ..domain.policies import (
    VEO_STATUS_FAILED,
    VEO_STATUS_GENERATING,
    VEO_STATUS_SUCCESS,
    VEO_STATUS_UPSTREAM_FAILED,
)
from ..domain.ports import KieGateway


def _extract_veo_result_url(detail: dict[str, Any]) -> str | None:
    """Extrae la primera URL de resultado del response de VEO.

    Shape esperado: `data.response.resultUrls[0]`.
    Devuelve `None` si la estructura no tiene URLs.
    """
    data = detail.get("data")
    if not isinstance(data, dict):
        return None
    response = data.get("response")
    if not isinstance(response, dict):
        return None
    urls = response.get("resultUrls")
    if isinstance(urls, list) and urls:
        return str(urls[0])
    return None


def _extract_veo_success_flag(detail: dict[str, Any]) -> int | None:
    """Extrae el `successFlag` del response de VEO polling.

    Shape esperado: `data.successFlag` (int).
    """
    data = detail.get("data")
    if not isinstance(data, dict):
        return None
    flag = data.get("successFlag")
    return int(flag) if isinstance(flag, int | float) else None


def _extract_veo_error(detail: dict[str, Any]) -> str:
    """Extrae un mensaje de error legible del response de VEO."""
    data = detail.get("data", {})
    if isinstance(data, dict):
        error_code = data.get("errorCode")
        if error_code:
            return f"errorCode={error_code}"
    return repr(detail)


async def poll_veo_task_for_url(
    gateway: KieGateway,
    task_id: str,
    *,
    interval_seconds: int,
    timeout_seconds: int,
) -> str:
    """Espera al task VEO y devuelve la URL del video generado.

    Lanza:
    - `KieTimeoutError` si pasan `timeout_seconds` sin éxito.
    - `KieError` si el task termina como failed (2) o upstream failed (3),
      o si llega a success (1) sin una URL extraible.
    """
    elapsed = 0
    interval = max(1, interval_seconds)
    while elapsed < timeout_seconds:
        detail = await gateway.get_veo_task_detail(task_id)
        flag = _extract_veo_success_flag(detail)

        if flag == VEO_STATUS_SUCCESS:
            url = _extract_veo_result_url(detail)
            if url:
                logger.debug("VEO task {} completado: {}", task_id, url[:80])
                return url
            raise KieError(f"VEO task {task_id} success pero sin resultUrls: {detail!r}")

        if flag == VEO_STATUS_FAILED:
            reason = _extract_veo_error(detail)
            raise KieError(f"VEO task {task_id} falló (pre-generación): {reason}")

        if flag == VEO_STATUS_UPSTREAM_FAILED:
            reason = _extract_veo_error(detail)
            raise KieError(f"VEO task {task_id} falló (upstream): {reason}")

        if flag == VEO_STATUS_GENERATING:
            logger.debug("VEO task {} generando... ({}s)", task_id, elapsed)
        else:
            logger.warning("VEO task {} successFlag inesperado: {}", task_id, flag)

        await asyncio.sleep(interval)
        elapsed += interval

    raise KieTimeoutError(f"VEO task {task_id} excedió {timeout_seconds}s de timeout")
