"""Helpers compartidos del workflow para interactuar con assets de Kie."""

from __future__ import annotations

from pathlib import Path

from ..domain.ports import KieGateway


async def download_kie_asset(
    *,
    client: KieGateway,
    url: str,
    output_path: Path,
) -> str:
    """Descarga una URL Kie a un path local, devolviendo el str del path."""
    await client.download_file(url, output_path)
    return str(output_path)
