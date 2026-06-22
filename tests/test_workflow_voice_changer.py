from __future__ import annotations

from pathlib import Path

import pytest

from kie_avatar_studio.app_layer.workflow_voice_changer import apply_voice_changer
from kie_avatar_studio.domain.models import VoiceChangerSettings, VoiceSettings


class _Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Path, Path, str, bool, str, VoiceSettings | None]] = []

    async def speech_to_speech_to_file(
        self,
        voice_id: str,
        audio_path: Path,
        output_path: Path,
        *,
        model_id: str = "unused",
        remove_background_noise: bool = False,
        output_format: str = "unused",
        voice_settings: VoiceSettings | None = None,
    ) -> Path:
        self.calls.append(
            (
                voice_id,
                audio_path,
                output_path,
                model_id,
                remove_background_noise,
                output_format,
                voice_settings,
            )
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"converted-audio")
        return output_path


class _ExplodingClient:
    async def speech_to_speech_to_file(
        self,
        voice_id: str,
        audio_path: Path,
        output_path: Path,
        *,
        model_id: str = "unused",
        remove_background_noise: bool = False,
        output_format: str = "unused",
        voice_settings: VoiceSettings | None = None,
    ) -> Path:
        _ = output_path
        raise RuntimeError(
            f"boom:{voice_id}:{audio_path.read_text()}:{model_id}:{remove_background_noise}:"
            f"{output_format}:{voice_settings}"
        )


async def test_apply_voice_changer_reads_calls_and_writes_result(tmp_path: Path) -> None:
    audio_path = tmp_path / "final_audio.mp3"
    audio_path.write_bytes(b"source-audio")
    output_path = tmp_path / "voice" / "changed.mp3"
    client = _Client()

    result = await apply_voice_changer(
        audio_path,
        output_path,
        VoiceChangerSettings(
            voice_id="voice_123",
            model_id="custom-model",
            remove_background_noise=True,
            output_format="aac_44100",
            voice_settings=VoiceSettings(
                stability=0.8,
                similarity_boost=0.9,
                style=0.1,
                speed=1.05,
            ),
        ),
        client,
    )

    assert result == output_path
    assert output_path.read_bytes() == b"converted-audio"
    assert client.calls == [
        (
            "voice_123",
            audio_path,
            output_path,
            "custom-model",
            True,
            "aac_44100",
            VoiceSettings(
                stability=0.8,
                similarity_boost=0.9,
                style=0.1,
                speed=1.05,
            ),
        )
    ]


async def test_apply_voice_changer_propagates_elevenlabs_errors(tmp_path: Path) -> None:
    audio_path = tmp_path / "final_audio.mp3"
    audio_path.write_bytes(b"source-audio")
    output_path = tmp_path / "voice" / "changed.mp3"

    with pytest.raises(
        RuntimeError,
        match=r"boom:voice_123:source-audio:custom-model:True:aac_44100:.*stability=0.8",
    ):
        await apply_voice_changer(
            audio_path,
            output_path,
            VoiceChangerSettings(
                voice_id="voice_123",
                model_id="custom-model",
                remove_background_noise=True,
                output_format="aac_44100",
                voice_settings=VoiceSettings(stability=0.8),
            ),
            _ExplodingClient(),
        )

    assert not output_path.exists()
    assert not output_path.parent.exists()
