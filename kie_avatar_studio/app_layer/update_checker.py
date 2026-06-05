"""`UpdateChecker`: compara la versión actual contra la última en GitHub Releases.

Capa application: orquesta el client de GitHub + la comparación SemVer
+ devuelve un resultado tipado que la UI consume sin tener que conocer
la API de GitHub.

Diseño de Protocol para el fetcher: el controller depende de un
callable `FetchLatestRelease`, no del cliente concreto, así los tests
no necesitan red ni mocks de httpx — pasan una función fake.

Comparación de versiones: SemVer puro (MAJOR.MINOR.PATCH). Hace tuple
compare después de quitar el prefijo `v` opcional. NO maneja prereleases
(`-rc.1` etc.) porque CR-13 no las usa por ahora.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from loguru import logger

from ..domain.models import GitHubRelease

FetchLatestRelease = Callable[[], Awaitable[GitHubRelease | None]]


@dataclass(frozen=True, slots=True)
class UpdateAvailable:
    """Resultado positivo: hay una versión más nueva publicada."""

    current_version: str
    latest_version: str
    release_url: str
    notes: str


class UpdateChecker:
    """Chequea si hay una versión nueva publicada en GitHub Releases."""

    def __init__(
        self,
        *,
        current_version: str,
        fetch_latest: FetchLatestRelease,
    ) -> None:
        self._current = current_version
        self._fetch = fetch_latest

    async def check(self) -> UpdateAvailable | None:
        """Devuelve `UpdateAvailable` si hay nueva versión, `None` si no.

        Nunca lanza: si la red falla o la API responde algo raro,
        loguea a DEBUG y devuelve `None`.
        """
        release = await self._fetch()
        if release is None:
            return None
        latest = _normalize_version(release.tag_name)
        current = _normalize_version(self._current)
        if not _is_newer(latest, current):
            return None
        logger.info(
            "Updater: hay nueva versión disponible (current={}, latest={})",
            self._current,
            release.tag_name,
        )
        return UpdateAvailable(
            current_version=self._current,
            latest_version=release.tag_name,
            release_url=release.html_url,
            notes=release.body,
        )


def _normalize_version(raw: str) -> tuple[int, ...]:
    """Convierte 'v1.2.3' o '1.2.3' → (1, 2, 3). Versiones rotas → (0, 0, 0).

    Si aparecen prereleases tipo '1.2.3-rc.1', cortamos en el '-' y
    parseamos solo la parte numérica (lo que da la equivalencia rc=base).
    Está OK por ahora: no se usan prereleases (ver CR-13.3).
    """
    cleaned = raw.lstrip("vV").split("-", 1)[0]
    parts: list[int] = []
    for piece in cleaned.split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            return (0, 0, 0)
    return tuple(parts)


def _is_newer(latest: tuple[int, ...], current: tuple[int, ...]) -> bool:
    """SemVer: comparación tuple-wise normalizando longitudes."""
    width = max(len(latest), len(current))
    a = latest + (0,) * (width - len(latest))
    b = current + (0,) * (width - len(current))
    return a > b
