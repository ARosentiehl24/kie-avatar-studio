from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from textual.widgets import Button, Input, Select

from kie_avatar_studio.app import KieAvatarStudioApp
from kie_avatar_studio.config import Settings
from kie_avatar_studio.domain.models import VoiceChangerSettings, VoiceSettings
from kie_avatar_studio.ui.screens.voice_changer_selector import (
    VoiceChangerSelectionResult,
    VoiceChangerSelectorScreen,
)


class _FakeElevenLabsClient:
    def __init__(
        self,
        *,
        voices: list[dict[str, Any]] | None = None,
        models: list[dict[str, Any]] | None = None,
    ) -> None:
        self._voices = voices or []
        self._models = models or []

    async def list_voices(
        self,
        *,
        voice_type: str | None = None,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        _ = voice_type, search
        return self._voices

    async def list_models(self) -> list[dict[str, Any]]:
        return self._models


class _FakeAudioPlayer:
    def __init__(self) -> None:
        self.played: list[str] = []
        self.stops = 0

    async def play_voice_preview(self, url: str) -> None:
        self.played.append(url)

    async def stop(self) -> None:
        self.stops += 1


class _SlowElevenLabsClient(_FakeElevenLabsClient):
    def __init__(self) -> None:
        super().__init__(
            voices=[{"voice_id": "voice_1", "name": "Ana"}],
            models=[{"model_id": "eleven_multilingual_sts_v2", "can_do_voice_conversion": True}],
        )
        self.release = asyncio.Event()

    async def list_voices(
        self,
        *,
        voice_type: str | None = None,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        await self.release.wait()
        return await super().list_voices(voice_type=voice_type, search=search)

    async def list_models(self) -> list[dict[str, Any]]:
        await self.release.wait()
        return await super().list_models()


def _build_app(tmp_path: Path) -> KieAvatarStudioApp:
    settings = Settings(
        kie_api_key="test-key",
        data_dir=tmp_path / "data",
        outputs_dir=tmp_path / "outputs",
        inputs_dir=tmp_path / "inputs",
        presets_dir=tmp_path / "presets",
        logs_dir=tmp_path / "logs",
        elevenlabs_api_key="el-key",
    )
    settings.ensure_dirs()
    app = KieAvatarStudioApp(settings=settings)

    async def fake_check() -> float | None:
        return None

    app._check_credits = fake_check  # type: ignore[method-assign]
    return app


async def test_voice_selector_returns_full_voice_changer_config(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    client = _FakeElevenLabsClient(
        voices=[
            {"voice_id": "voice_1", "name": "Ana"},
            {"voice_id": "voice_2", "name": "Carla"},
        ],
        models=[
            {"model_id": "eleven_multilingual_sts_v2", "name": "STS v2"},
            {"model_id": "eleven_turbo_v2", "name": "No STS"},
        ],
    )
    captured: dict[str, VoiceChangerSelectionResult | None] = {}

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.push_screen(
            VoiceChangerSelectorScreen(
                elevenlabs_client=client,
                initial_selection=None,
            ),
            lambda result: captured.setdefault("result", result),
        )
        await pilot.pause()
        await pilot.pause()
        voice_select = app.screen.query_one("#voice-changer-selector-select", Select)
        model_select = app.screen.query_one("#voice-changer-selector-model", Select)
        noise_select = app.screen.query_one("#voice-changer-selector-noise", Select)
        format_select = app.screen.query_one("#voice-changer-selector-format", Select)
        stability_input = app.screen.query_one("#voice-changer-stability", Input)
        similarity_input = app.screen.query_one("#voice-changer-similarity", Input)
        style_input = app.screen.query_one("#voice-changer-style", Input)
        speed_input = app.screen.query_one("#voice-changer-speed", Input)
        voice_select.value = "voice_2"
        model_select.value = "eleven_multilingual_sts_v2"
        noise_select.value = "__noise_off__"
        format_select.value = "aac_44100"
        stability_input.value = "0.82"
        similarity_input.value = "0.91"
        style_input.value = "0.2"
        speed_input.value = "1.05"
        await pilot.click("#voice-changer-selector-confirm")
        await pilot.pause()

    result = captured.get("result")
    assert result is not None
    assert result.voice_changer is not None
    assert result.voice_changer.voice_id == "voice_2"
    assert result.voice_changer.model_id == "eleven_multilingual_sts_v2"
    assert result.voice_changer.remove_background_noise is False
    assert result.voice_changer.output_format == "aac_44100"
    assert result.voice_changer.voice_settings == VoiceSettings(
        stability=0.82,
        similarity_boost=0.91,
        style=0.2,
        speed=1.05,
    )


async def test_voice_selector_sorts_voices_alphabetically(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    client = _FakeElevenLabsClient(
        voices=[
            {"voice_id": "voice_z", "name": "Zoe"},
            {"voice_id": "voice_a", "name": "Ana"},
            {"voice_id": "voice_c", "name": "Carla"},
        ],
        models=[{"model_id": "eleven_multilingual_sts_v2", "can_do_voice_conversion": True}],
    )

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.push_screen(
            VoiceChangerSelectorScreen(
                elevenlabs_client=client,
                initial_selection=None,
            )
        )
        await pilot.pause()
        await pilot.pause()
        voice_select = app.screen.query_one("#voice-changer-selector-select", Select)
        option_labels = [str(label) for label, _value in voice_select._options]

    assert option_labels[:4] == [
        "Sin voice changer",
        "Ana  ·  voice_a",
        "Carla  ·  voice_c",
        "Zoe  ·  voice_z",
    ]


async def test_voice_selector_filters_voices_by_text(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    client = _FakeElevenLabsClient(
        voices=[
            {"voice_id": "voice_ana", "name": "Ana"},
            {"voice_id": "voice_carla", "name": "Carla"},
            {"voice_id": "narrador_1", "name": "Mario"},
        ],
        models=[{"model_id": "eleven_multilingual_sts_v2", "can_do_voice_conversion": True}],
    )

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.push_screen(
            VoiceChangerSelectorScreen(
                elevenlabs_client=client,
                initial_selection=None,
            )
        )
        await pilot.pause()
        await pilot.pause()
        search = app.screen.query_one("#voice-changer-selector-search", Input)
        search.value = "nar"
        await pilot.pause()
        voice_select = app.screen.query_one("#voice-changer-selector-select", Select)
        option_labels = [str(label) for label, _value in voice_select._options]
        status = app.screen.query_one("#voice-changer-selector-search-status")
        rendered_status = str(status.content)

    assert option_labels == ["Sin voice changer", "Mario  ·  narrador_1"]
    assert "1 voces coinciden" in rendered_status


async def test_voice_selector_preserves_initial_custom_values(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    client = _FakeElevenLabsClient(voices=[], models=[])
    initial = VoiceChangerSettings(
        voice_id="voice_custom",
        model_id="modelo_custom",
        remove_background_noise=False,
        output_format="wav_44100",
        voice_settings=VoiceSettings(
            stability=0.7,
            similarity_boost=0.8,
            style=0.1,
            speed=0.95,
        ),
    )
    captured: dict[str, VoiceChangerSelectionResult | None] = {}

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.push_screen(
            VoiceChangerSelectorScreen(
                elevenlabs_client=client,
                initial_selection=initial,
            ),
            lambda result: captured.setdefault("result", result),
        )
        await pilot.pause()
        await pilot.pause()
        voice_select = app.screen.query_one("#voice-changer-selector-select", Select)
        model_select = app.screen.query_one("#voice-changer-selector-model", Select)
        noise_select = app.screen.query_one("#voice-changer-selector-noise", Select)
        format_select = app.screen.query_one("#voice-changer-selector-format", Select)
        stability_input = app.screen.query_one("#voice-changer-stability", Input)
        similarity_input = app.screen.query_one("#voice-changer-similarity", Input)
        style_input = app.screen.query_one("#voice-changer-style", Input)
        speed_input = app.screen.query_one("#voice-changer-speed", Input)
        assert voice_select.value == "voice_custom"
        assert model_select.value == "modelo_custom"
        assert noise_select.value == "__noise_off__"
        assert format_select.value == "wav_44100"
        assert stability_input.value == "0.7"
        assert similarity_input.value == "0.8"
        assert style_input.value == "0.1"
        assert speed_input.value == "0.95"
        await pilot.click("#voice-changer-selector-confirm")
        await pilot.pause()

    result = captured.get("result")
    assert result is not None
    assert result.voice_changer is not None
    assert result.voice_changer.voice_id == "voice_custom"
    assert result.voice_changer.model_id == "modelo_custom"
    assert result.voice_changer.remove_background_noise is False
    assert result.voice_changer.output_format == "wav_44100"
    assert result.voice_changer.voice_settings == initial.voice_settings


async def test_voice_selector_can_preview_selected_elevenlabs_voice(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    client = _FakeElevenLabsClient(
        voices=[
            {
                "voice_id": "voice_1",
                "name": "Ana",
                "preview_url": "https://cdn.elevenlabs.io/previews/voice_1.mp3",
            },
            {
                "voice_id": "voice_2",
                "name": "Carla",
                "preview_url": "https://cdn.elevenlabs.io/previews/voice_2.mp3",
            },
        ],
        models=[{"model_id": "eleven_multilingual_sts_v2", "can_do_voice_conversion": True}],
    )
    audio_player = _FakeAudioPlayer()

    async with app.run_test(size=(120, 45)) as pilot:
        await pilot.pause()
        app.push_screen(
            VoiceChangerSelectorScreen(
                elevenlabs_client=client,
                initial_selection=None,
                audio_player=audio_player,
            )
        )
        await pilot.pause()
        await pilot.pause()
        box = app.screen.query_one("#voice-changer-selector-box")
        preview_row = app.screen.query_one("#voice-changer-preview-row")
        assert box.outer_size.width <= 100
        assert preview_row.outer_size.height == 3
        preview_button = app.screen.query_one("#voice-changer-selector-preview", Button)
        assert not preview_button.disabled
        voice_select = app.screen.query_one("#voice-changer-selector-select", Select)
        voice_select.value = "voice_2"
        await pilot.click("#voice-changer-selector-preview")
        await pilot.pause()
        await pilot.click("#voice-changer-selector-preview-stop")
        await pilot.pause()

    assert audio_player.played == ["https://cdn.elevenlabs.io/previews/voice_2.mp3"]
    assert audio_player.stops == 1


async def test_voice_selector_ignores_late_load_after_dismiss(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    client = _SlowElevenLabsClient()

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.push_screen(
            VoiceChangerSelectorScreen(
                elevenlabs_client=client,
                initial_selection=None,
            )
        )
        await pilot.pause()
        app.screen.action_cancel()
        await pilot.pause()
        client.release.set()
        await pilot.pause()
        await pilot.pause()

    assert True
