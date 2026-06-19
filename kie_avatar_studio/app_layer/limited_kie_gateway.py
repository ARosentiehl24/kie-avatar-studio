"""Decorador de `KieGateway` con límites selectivos por tipo de operación."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ..domain.models import KieTaskCreated, KieUploadResult, VoiceSettings
from ..domain.policies import (
    DEFAULT_I2V_ASPECT_RATIO,
    DEFAULT_I2V_DURATION_SECONDS,
    DEFAULT_I2V_MODE,
    DEFAULT_I2V_MODEL,
)
from ..domain.ports import KieGateway


class LimitedKieGateway:
    """Aplica semáforos específicos sin mezclar audio, imagen, video y IO."""

    def __init__(
        self,
        inner: KieGateway,
        *,
        audio_limiter: asyncio.Semaphore,
        image_limiter: asyncio.Semaphore,
        video_limiter: asyncio.Semaphore,
        upload_limiter: asyncio.Semaphore,
        download_limiter: asyncio.Semaphore,
    ) -> None:
        self._inner = inner
        self._audio_limiter = audio_limiter
        self._image_limiter = image_limiter
        self._video_limiter = video_limiter
        self._upload_limiter = upload_limiter
        self._download_limiter = download_limiter

    async def upload_file(
        self,
        file_path: str | Path,
        upload_path: str = "images/avatar-models",
        file_name: str | None = None,
    ) -> KieUploadResult:
        async with self._upload_limiter:
            return await self._inner.upload_file(file_path, upload_path, file_name)

    async def create_tts_task(
        self,
        text: str,
        voice: str,
        *,
        model: str | None = None,
        voice_settings: VoiceSettings | None = None,
    ) -> KieTaskCreated:
        async with self._audio_limiter:
            return await self._inner.create_tts_task(
                text, voice, model=model, voice_settings=voice_settings
            )

    async def create_avatar_task(
        self, image_url: str, audio_url: str, prompt: str
    ) -> KieTaskCreated:
        async with self._video_limiter:
            return await self._inner.create_avatar_task(image_url, audio_url, prompt)

    async def create_nano_banana_task(
        self,
        prompt: str,
        *,
        image_input: list[str] | None = None,
        aspect_ratio: str = "auto",
        resolution: str = "1K",
        output_format: str = "jpg",
        model: str = "",
    ) -> KieTaskCreated:
        async with self._image_limiter:
            if not model:
                return await self._inner.create_nano_banana_task(
                    prompt,
                    image_input=image_input,
                    aspect_ratio=aspect_ratio,
                    resolution=resolution,
                    output_format=output_format,
                )
            return await self._inner.create_nano_banana_task(
                prompt,
                image_input=image_input,
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                output_format=output_format,
                model=model,
            )

    async def create_kling_video_task(
        self,
        image_url: str,
        prompt: str,
        *,
        model: str = DEFAULT_I2V_MODEL,
        duration: int = DEFAULT_I2V_DURATION_SECONDS,
        sound: bool = False,
        mode: str = DEFAULT_I2V_MODE,
        aspect_ratio: str = DEFAULT_I2V_ASPECT_RATIO,
    ) -> KieTaskCreated:
        async with self._video_limiter:
            return await self._inner.create_kling_video_task(
                image_url,
                prompt,
                model=model,
                duration=duration,
                sound=sound,
                mode=mode,
                aspect_ratio=aspect_ratio,
            )

    async def get_task_detail(self, task_id: str) -> dict[str, Any]:
        return await self._inner.get_task_detail(task_id)

    async def create_veo_video_task(
        self,
        prompt: str,
        *,
        image_urls: list[str] | None = None,
        model: str = "veo3_fast",
        generation_type: str = "FIRST_AND_LAST_FRAMES_2_VIDEO",
        aspect_ratio: str = "9:16",
        resolution: str = "720p",
        duration: int = 8,
        enable_translation: bool = True,
        watermark: str | None = None,
    ) -> KieTaskCreated:
        async with self._video_limiter:
            return await self._inner.create_veo_video_task(
                prompt,
                image_urls=image_urls,
                model=model,
                generation_type=generation_type,
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                duration=duration,
                enable_translation=enable_translation,
                watermark=watermark,
            )

    async def get_veo_task_detail(self, task_id: str) -> dict[str, Any]:
        return await self._inner.get_veo_task_detail(task_id)

    async def get_account_credits(self) -> float:
        return await self._inner.get_account_credits()

    async def download_file(self, url: str, output_path: str | Path) -> Path:
        async with self._download_limiter:
            return await self._inner.download_file(url, output_path)

    async def aclose(self) -> None:
        await self._inner.aclose()
