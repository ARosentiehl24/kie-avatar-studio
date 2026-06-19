from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Protocol

from loguru import logger

from ..domain.models import VoiceChangerSettings


class ElevenLabsClient(Protocol):
    """Contrato mínimo del cliente de speech-to-speech usado por la app."""

    async def speech_to_speech(
        self,
        voice_id: str,
        audio: bytes,
        *,
        model_id: str = ...,
        remove_background_noise: bool = ...,
        output_format: str = ...,
    ) -> bytes: ...


async def apply_voice_changer(
    audio_path: Path,
    output_path: Path,
    voice_changer: VoiceChangerSettings,
    elevenlabs_client: ElevenLabsClient,
) -> Path:
    """Aplica speech-to-speech de ElevenLabs y persiste el audio convertido."""
    logger.info(
        "Aplicando voice changer de ElevenLabs voice_id={} input={}",
        voice_changer.voice_id,
        audio_path,
    )
    audio_bytes = await asyncio.to_thread(audio_path.read_bytes)
    converted = await elevenlabs_client.speech_to_speech(
        voice_changer.voice_id,
        audio_bytes,
        model_id=voice_changer.model_id,
        remove_background_noise=voice_changer.remove_background_noise,
        output_format=voice_changer.output_format,
    )
    await asyncio.to_thread(output_path.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(output_path.write_bytes, converted)
    return output_path


__all__ = ["ElevenLabsClient", "apply_voice_changer"]
