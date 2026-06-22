from __future__ import annotations

import asyncio
from pathlib import Path
from typing import BinaryIO

import httpx


async def write_response_to_file(
    response: httpx.Response,
    output_path: Path,
    *,
    chunk_size: int | None = None,
) -> Path:
    """Escribe una respuesta HTTP streaming a disco sin bloquear el event loop."""
    await asyncio.to_thread(output_path.parent.mkdir, parents=True, exist_ok=True)
    output_file: BinaryIO = await asyncio.to_thread(output_path.open, "wb")
    try:
        async for chunk in response.aiter_bytes(chunk_size):
            await asyncio.to_thread(output_file.write, chunk)
    finally:
        await asyncio.to_thread(output_file.close)
    return output_path
