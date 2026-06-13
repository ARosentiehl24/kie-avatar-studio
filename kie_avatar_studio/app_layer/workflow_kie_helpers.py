"""Helpers compartidos para invocar endpoints Kie desde el step runner.

Centraliza el flow `create → poll → download` para Avatar Pro (a-roll)
y Kling 3.0 video (b-roll), evitando duplicación entre los tres paths
del `WorkflowStepRunner`.

Cada helper recibe el `KieGateway` y un `Semaphore` global, y devuelve
el path local descargado. NO toca persistencia (eso lo hace el caller).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Final

from ..config import Settings
from ..domain.policies import DEFAULT_I2V_ASPECT_RATIO, DEFAULT_I2V_MODE
from ..domain.ports import KieGateway
from .polling import poll_task_for_url

DEFAULT_AVATAR_KIND: Final[str] = "video"
DEFAULT_I2V_KIND: Final[str] = "video"


async def render_avatar_video(
    *,
    client: KieGateway,
    settings: Settings,
    limiter: asyncio.Semaphore,
    download_limiter: asyncio.Semaphore,
    image_url: str,
    audio_url: str,
    prompt: str,
    output_path: Path,
    existing_task_id: str | None = None,
) -> tuple[str, str]:
    """Crea (o reusa) un task de Avatar Pro, pollea, descarga al path dado.

    Devuelve `(task_id, str(output_path))`.

    Si `existing_task_id` no es `None`, no crea un task nuevo en Kie
    (resume tras crash: el task ya está creado y consumió créditos).
    """
    task_id = existing_task_id
    if task_id is None:
        async with limiter:
            created = await client.create_avatar_task(image_url, audio_url, prompt)
        task_id = created.task_id

    async with limiter:
        video_url = await poll_task_for_url(
            client,
            task_id,
            kind=DEFAULT_AVATAR_KIND,
            interval_seconds=settings.poll_interval_seconds,
            timeout_seconds=settings.task_timeout_seconds,
        )

    async with download_limiter:
        await client.download_file(video_url, output_path)
    return task_id, str(output_path)


async def render_i2v_video(
    *,
    client: KieGateway,
    settings: Settings,
    limiter: asyncio.Semaphore,
    download_limiter: asyncio.Semaphore,
    image_url: str,
    prompt: str,
    output_path: Path,
    duration: int,
    sound: bool = False,
    mode: str | None = None,
    aspect_ratio: str | None = None,
    existing_task_id: str | None = None,
) -> tuple[str, str]:
    """Crea (o reusa) un task de Kling 3.0 (b-roll), pollea, descarga al path dado.

    Devuelve `(task_id, str(output_path))`.

    `sound=True` → Kling genera sound effects ambientales nativos basados en
    el prompt (NO es voiceover hablado de TTS). `sound=False` → video
    silencioso (el TTS aparte si hay text se monta en post).

    `mode` y `aspect_ratio` opcionales; si son `None` se usan los defaults
    del cliente (`pro` / `16:9`).
    """
    task_id = existing_task_id
    if task_id is None:
        resolved_mode = mode or DEFAULT_I2V_MODE
        resolved_aspect_ratio = aspect_ratio or DEFAULT_I2V_ASPECT_RATIO
        async with limiter:
            created = await client.create_kling_video_task(
                image_url,
                prompt,
                duration=duration,
                sound=sound,
                mode=resolved_mode,
                aspect_ratio=resolved_aspect_ratio,
            )
        task_id = created.task_id

    async with limiter:
        video_url = await poll_task_for_url(
            client,
            task_id,
            kind=DEFAULT_I2V_KIND,
            interval_seconds=settings.poll_interval_seconds,
            timeout_seconds=settings.task_timeout_seconds,
        )

    async with download_limiter:
        await client.download_file(video_url, output_path)
    return task_id, str(output_path)


async def download_kie_asset(
    *,
    client: KieGateway,
    url: str,
    output_path: Path,
) -> str:
    """Descarga una URL Kie a un path local, devolviendo el str del path."""
    await client.download_file(url, output_path)
    return str(output_path)
