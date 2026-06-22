"""Descarga binaria de archivos públicos por HTTP (no necesita API key).

Pensado para cachear localmente recursos que Kie sirve con
`Content-Disposition: attachment` (previews de voces, audios generados):
bajarlos a disco para que el handler de `audio/mpeg` del SO los reproduzca
en lugar de que el navegador los descargue.

Es HTTP puro (CR-5.1): convierte cualquier error de red a `OSError` para
que el caller (`app_layer/system_opener`) no tenga que conocer `httpx`.
La escritura es atómica via `.part` + `rename` para no dejar archivos
corruptos si la descarga se interrumpe.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from ..domain.policies import AUDIO_DOWNLOAD_TIMEOUT_SECONDS, KIE_DOWNLOAD_CHUNK_BYTES
from ._streaming import write_response_to_file


async def download_audio(url: str, destination: Path) -> None:
    """Descarga `url` a `destination` por streaming. Atómico: tmp + rename.

    Convierte cualquier error de red (`httpx.HTTPError`) a `OSError` para que
    `app_layer` pueda traducirlo a notify visible sin importar httpx.
    """
    tmp_path = destination.with_suffix(destination.suffix + ".part")
    try:
        async with (
            httpx.AsyncClient(timeout=AUDIO_DOWNLOAD_TIMEOUT_SECONDS) as client,
            client.stream("GET", url) as response,
        ):
            response.raise_for_status()
            await write_response_to_file(response, tmp_path, chunk_size=KIE_DOWNLOAD_CHUNK_BYTES)
        await asyncio.to_thread(tmp_path.replace, destination)
    except httpx.HTTPError as exc:
        await asyncio.to_thread(tmp_path.unlink, missing_ok=True)
        raise OSError(f"no pude descargar el audio: {exc}") from exc
    except OSError:
        await asyncio.to_thread(tmp_path.unlink, missing_ok=True)
        raise
