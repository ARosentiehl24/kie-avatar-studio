from __future__ import annotations

from pathlib import Path

import pytest

from kie_avatar_studio.app_layer.workflow_voice_changer import apply_voice_changer
from kie_avatar_studio.domain.models import VoiceChangerSettings


class _Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bytes, str, bool, str]] = []

    async def speech_to_speech(
        self,
        voice_id: str,
        audio: bytes,
        *,
        model_id: str = "unused",
        remove_background_noise: bool = False,
        output_format: str = "unused",
    ) -> bytes:
        self.calls.append(
            (
                voice_id,
                audio,
                model_id,
                remove_background_noise,
                output_format,
            )
        )
        return b"converted-audio"


class _ExplodingClient:
    async def speech_to_speech(
        self,
        voice_id: str,
        audio: bytes,
        *,
        model_id: str = "unused",
        remove_background_noise: bool = False,
        output_format: str = "unused",
    ) -> bytes:
        raise RuntimeError(
            f"boom:{voice_id}:{audio.decode()}:{model_id}:{remove_background_noise}:{output_format}"
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
        ),
        client,
    )

    assert result == output_path
    assert output_path.read_bytes() == b"converted-audio"
    assert client.calls == [
        (
            "voice_123",
            b"source-audio",
            "custom-model",
            True,
            "aac_44100",
        )
    ]


async def test_apply_voice_changer_propagates_elevenlabs_errors(tmp_path: Path) -> None:
    audio_path = tmp_path / "final_audio.mp3"
    audio_path.write_bytes(b"source-audio")
    output_path = tmp_path / "voice" / "changed.mp3"

    with pytest.raises(
        RuntimeError,
        match="boom:voice_123:source-audio:custom-model:True:aac_44100",
    ):
        await apply_voice_changer(
            audio_path,
            output_path,
            VoiceChangerSettings(
                voice_id="voice_123",
                model_id="custom-model",
                remove_background_noise=True,
                output_format="aac_44100",
            ),
            _ExplodingClient(),
        )

    assert not output_path.exists()
    assert not output_path.parent.exists()
