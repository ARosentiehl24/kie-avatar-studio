from __future__ import annotations

from pathlib import Path

from loguru import logger

from ..domain.models import VoiceChangerSettings
from ..domain.ports import ElevenLabsSpeechToSpeechClient


async def apply_voice_changer(
    audio_path: Path,
    output_path: Path,
    voice_changer: VoiceChangerSettings,
    elevenlabs_client: ElevenLabsSpeechToSpeechClient,
) -> Path:
    """Aplica speech-to-speech de ElevenLabs y persiste el audio convertido."""
    logger.info(
        "Aplicando voice changer de ElevenLabs voice_id={} input={}",
        voice_changer.voice_id,
        audio_path,
    )
    return await elevenlabs_client.speech_to_speech_to_file(
        voice_changer.voice_id,
        audio_path,
        output_path,
        model_id=voice_changer.model_id,
        remove_background_noise=voice_changer.remove_background_noise,
        output_format=voice_changer.output_format,
        voice_settings=voice_changer.voice_settings,
    )


__all__ = ["apply_voice_changer"]
