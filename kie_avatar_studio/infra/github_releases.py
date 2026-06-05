"""Cliente para la API de GitHub Releases: chequea si hay versión nueva.

Tiene un único método público `get_latest_release` que devuelve la
última release del repo, o `None` si la red falló / no hay releases /
algo raro. Best-effort: nunca lanza al caller (el updater es opcional).

Endpoint usado:
    GET https://api.github.com/repos/{owner}/{repo}/releases/latest

No requiere auth para repos públicos. Hay rate limit anónimo de 60
req/h por IP — suficiente para un check cada 24h al arrancar la app.
"""

from __future__ import annotations

from typing import Final

import httpx
from loguru import logger

from ..domain.models import GitHubRelease

_GITHUB_API_BASE: Final[str] = "https://api.github.com"
_REQUEST_TIMEOUT: Final[float] = 5.0
_USER_AGENT: Final[str] = "kie-avatar-studio-updater"
_HTTP_OK: Final[int] = 200
_HTTP_NOT_FOUND: Final[int] = 404


async def get_latest_release(owner: str, repo: str) -> GitHubRelease | None:
    """Obtiene la última release del repo. Devuelve `None` si falla.

    No lanza nunca: el updater es opcional y un fallo de red NO debe
    romper el arranque de la app. Errores se loguean a DEBUG.
    """
    url = f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/releases/latest"
    try:
        async with httpx.AsyncClient(
            timeout=_REQUEST_TIMEOUT,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        ) as client:
            resp = await client.get(url)
    except (httpx.HTTPError, OSError) as exc:
        logger.debug("Updater: GET {} falló: {}", url, exc)
        return None
    if resp.status_code == _HTTP_NOT_FOUND:
        # Repo sin releases todavía — caso normal al instalar.
        logger.debug("Updater: {} aún no tiene releases", repo)
        return None
    if resp.status_code != _HTTP_OK:
        logger.debug("Updater: GET {} → HTTP {}", url, resp.status_code)
        return None
    try:
        data = resp.json()
    except ValueError:
        logger.debug("Updater: respuesta de GitHub no es JSON válido")
        return None
    try:
        return GitHubRelease(
            tag_name=str(data["tag_name"]),
            html_url=str(data["html_url"]),
            body=str(data.get("body") or ""),
            published_at=str(data.get("published_at") or ""),
        )
    except (KeyError, TypeError) as exc:
        logger.debug("Updater: shape inesperado en respuesta de GitHub: {}", exc)
        return None
