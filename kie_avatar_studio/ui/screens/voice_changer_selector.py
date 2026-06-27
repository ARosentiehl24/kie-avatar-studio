from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Final

from loguru import logger
from textual.app import ComposeResult
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static

from ...domain.errors import UrlValidationError, VoiceSettingsValidationError
from ...domain.models import (
    DEFAULT_VOICE_CHANGER_MODEL_ID,
    DEFAULT_VOICE_CHANGER_OUTPUT_FORMAT,
    VoiceChangerSettings,
)
from ...domain.ports import AudioPreviewPlayer, ElevenLabsVoicesClient, ExternalJsonObject
from .._icons import ERROR, OK
from ._voice_changer_selector_form import (
    build_selection,
    collect_voice_settings,
    initial_voice_setting,
    read_form_values,
    selected_voice_id,
)
from ._voice_changer_selector_options import (
    build_model_options,
    build_voice_options,
)
from ._voice_changer_selector_widgets import compose_voice_changer_selector

_NOTIFICATION_TIMEOUT: Final[int] = 4
_LONG_NOTIFICATION_TIMEOUT: Final[int] = 6
_LOADING_SENTINEL: Final[str] = "__loading__"
_NO_VOICE_CHANGER_SENTINEL: Final[str] = "__no_voice_changer__"
_NOISE_ON_SENTINEL: Final[str] = "__noise_on__"
_NOISE_OFF_SENTINEL: Final[str] = "__noise_off__"
_OUTPUT_FORMAT_OPTIONS: Final[tuple[tuple[str, str], ...]] = (
    ("MP3 44.1kHz 128kbps (recomendado)", DEFAULT_VOICE_CHANGER_OUTPUT_FORMAT),
    ("AAC 44.1kHz", "aac_44100"),
)


@dataclass(frozen=True)
class VoiceChangerSelectionResult:
    voice_changer: VoiceChangerSettings | None


class VoiceChangerSelectorScreen(ModalScreen[VoiceChangerSelectionResult | None]):
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "cancel", "Cancelar"),
    ]

    def __init__(
        self,
        *,
        elevenlabs_client: ElevenLabsVoicesClient,
        initial_selection: VoiceChangerSettings | None,
        audio_player: AudioPreviewPlayer | None = None,
    ) -> None:
        super().__init__()
        self._elevenlabs_client = elevenlabs_client
        self._audio_player = audio_player
        self._initial_selection = (
            initial_selection.model_copy(deep=True) if initial_selection else None
        )
        self._voices_loaded = False
        self._models_loaded = False
        self._raw_voices: list[ExternalJsonObject] = []
        self._voice_preview_urls: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        yield from compose_voice_changer_selector(
            audio_preview_available=self._audio_player is not None,
            initial_noise_enabled=self._initial_noise_enabled(),
            output_format_options=self._render_output_format_options(),
            initial_output_format=self._initial_output_format(),
            initial_voice_setting=lambda field: initial_voice_setting(
                self._initial_selection, field
            ),
            loading_value=_LOADING_SENTINEL,
            noise_on_value=_NOISE_ON_SENTINEL,
            noise_off_value=_NOISE_OFF_SENTINEL,
        )

    def on_mount(self) -> None:
        self.app.run_worker(self._load_options(), exclusive=False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "voice-changer-selector-cancel":
            self._stop_preview_async()
            self.dismiss(None)
            return
        if button_id == "voice-changer-selector-confirm":
            self._handle_confirm()
            return
        if button_id == "voice-changer-selector-preview":
            self._handle_preview()
            return
        if button_id == "voice-changer-selector-preview-stop":
            self._stop_preview_async()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "voice-changer-selector-search" or not self._voices_loaded:
            return
        try:
            self._apply_voice_options(self._raw_voices)
        except NoMatches:
            logger.debug("VoiceChangerSelector: filtro ignorado porque el modal ya no está montado")

    def action_cancel(self) -> None:
        self._stop_preview_async()
        self.dismiss(None)

    async def _load_options(self) -> None:
        voices, models, voice_error, model_error = await self._fetch_options()
        try:
            if not self._form_widgets_available():
                logger.debug("VoiceChangerSelector: carga de voces ignorada; modal desmontado")
                return
            visible_voices = self._apply_voice_options(voices)
            visible_models = self._apply_model_options(models)
            if self._show_load_error(voice_error, model_error):
                return
            self._set_status(f"{OK} {visible_voices} voces y {visible_models} modelos cargados")
        except NoMatches:
            logger.debug("VoiceChangerSelector: widgets ya no existen al terminar carga async")

    async def _fetch_options(
        self,
    ) -> tuple[
        list[ExternalJsonObject],
        list[ExternalJsonObject],
        Exception | None,
        Exception | None,
    ]:
        voices: list[ExternalJsonObject] = []
        models: list[ExternalJsonObject] = []
        voice_error: Exception | None = None
        model_error: Exception | None = None
        try:
            voices = await self._elevenlabs_client.list_voices()
        except Exception as exc:
            voice_error = exc
        try:
            models = await self._elevenlabs_client.list_models()
        except Exception as exc:
            model_error = exc
        return voices, models, voice_error, model_error

    def _show_load_error(
        self,
        voice_error: Exception | None,
        model_error: Exception | None,
    ) -> bool:
        if voice_error is not None and model_error is not None:
            self._set_status(
                f"{ERROR} no pude cargar voces/modelos de ElevenLabs: "
                f"voces={voice_error} · modelos={model_error}",
                error=True,
            )
            return True
        if voice_error is not None:
            self._set_status(
                f"{ERROR} no pude listar voces de ElevenLabs: {voice_error}",
                error=True,
            )
            return True
        if model_error is not None:
            self._set_status(
                f"{ERROR} no pude listar modelos de ElevenLabs: {model_error}",
                error=True,
            )
            return True
        return False

    def _form_widgets_available(self) -> bool:
        """True si el modal sigue montado y los widgets del form existen."""
        try:
            self.query_one("#voice-changer-selector-select", Select)
            self.query_one("#voice-changer-selector-model", Select)
            self.query_one("#voice-changer-selector-status", Static)
        except NoMatches:
            return False
        return True

    def _apply_voice_options(self, raw_voices: list[ExternalJsonObject]) -> int:
        select = self.query_one("#voice-changer-selector-select", Select)
        self._raw_voices = raw_voices
        search_query = self.query_one("#voice-changer-selector-search", Input).value
        current_voice_id = self._current_voice_id()
        result = build_voice_options(
            raw_voices,
            current_voice_id=current_voice_id,
            disabled_value=_NO_VOICE_CHANGER_SENTINEL,
            search_query=search_query,
        )
        self._voice_preview_urls = result.preview_urls
        option_values = {value for _, value in result.options}
        selected_value = (
            current_voice_id
            if current_voice_id is not None and current_voice_id in option_values
            else _NO_VOICE_CHANGER_SENTINEL
        )
        select.set_options(result.options)
        select.value = selected_value
        self._voices_loaded = True
        self._refresh_search_status(result.visible_count, search_query)
        self._refresh_confirm_state()
        return result.visible_count

    def _current_voice_id(self) -> str | None:
        if self._voices_loaded:
            value = self.query_one("#voice-changer-selector-select", Select).value
            if isinstance(value, str) and value not in {
                _LOADING_SENTINEL,
                _NO_VOICE_CHANGER_SENTINEL,
            }:
                return value
        return self._initial_selection.voice_id if self._initial_selection else None

    def _refresh_search_status(self, visible_count: int, search_query: str) -> None:
        try:
            status = self.query_one("#voice-changer-selector-search-status", Static)
        except NoMatches:
            return
        suffix = " coinciden" if search_query.strip() else " disponibles"
        status.update(f"[dim]{visible_count} voces{suffix}, ordenadas alfabéticamente.[/dim]")

    def _apply_model_options(self, raw_models: list[ExternalJsonObject]) -> int:
        select = self.query_one("#voice-changer-selector-model", Select)
        current_model_id = self._initial_model_id()
        options, visible_count = build_model_options(
            raw_models,
            current_model_id=current_model_id,
            default_model_id=DEFAULT_VOICE_CHANGER_MODEL_ID,
        )
        select.set_options(options)
        select.value = current_model_id
        self._models_loaded = True
        self._refresh_confirm_state()
        return visible_count

    def _handle_confirm(self) -> None:
        result = self._read_voice_changer_result()
        if result is None:
            return
        self._stop_preview_async()
        self.dismiss(result)

    def _read_voice_changer_result(self) -> VoiceChangerSelectionResult | None:
        values = read_form_values(
            self.query_one,
            self._set_form_error,
            loading=_LOADING_SENTINEL,
            disabled=_NO_VOICE_CHANGER_SENTINEL,
        )
        if values is None:
            return None
        if values[0] == _NO_VOICE_CHANGER_SENTINEL:
            return VoiceChangerSelectionResult(voice_changer=None)
        try:
            voice_settings = collect_voice_settings(self.query_one)
        except VoiceSettingsValidationError as exc:
            self._set_status(f"{ERROR} {exc}", error=True)
            return None
        selection = build_selection(
            self._initial_selection,
            values,
            noise_on=_NOISE_ON_SENTINEL,
            voice_settings=voice_settings,
        )
        return VoiceChangerSelectionResult(voice_changer=selection)

    def _set_form_error(self, message: str) -> None:
        self._set_status(f"{ERROR} {message}", error=True)

    def _handle_preview(self) -> None:
        if self._audio_player is None:
            self._set_status(f"{ERROR} preview no disponible", error=True)
            return
        voice_id = selected_voice_id(
            self.query_one,
            loading=_LOADING_SENTINEL,
            disabled=_NO_VOICE_CHANGER_SENTINEL,
        )
        if voice_id is None:
            self._set_status(f"{ERROR} elegí una voz para escuchar", error=True)
            return
        preview_url = self._voice_preview_urls.get(voice_id)
        if preview_url is None:
            self._set_status(
                f"{ERROR} la voz seleccionada no trae preview_url desde ElevenLabs",
                error=True,
            )
            return
        self.app.run_worker(self._open_preview(preview_url), exclusive=False)

    async def _open_preview(self, url: str) -> None:
        if self._audio_player is None:
            return
        try:
            await self._audio_player.play_voice_preview(url)
        except (OSError, UrlValidationError) as exc:
            self._set_status(f"{ERROR} no pude reproducir el preview: {exc}", error=True)

    def _stop_preview_async(self) -> None:
        if self._audio_player is None:
            return
        self.app.run_worker(self._audio_player.stop(), exclusive=False)

    def _render_output_format_options(self) -> list[tuple[str, str]]:
        options = list(_OUTPUT_FORMAT_OPTIONS)
        current = self._initial_output_format()
        if all(value != current for _, value in options):
            options.insert(0, (f"Actual (no listado)  ·  {current}", current))
        return options

    def _initial_model_id(self) -> str:
        if self._initial_selection is None:
            return DEFAULT_VOICE_CHANGER_MODEL_ID
        return self._initial_selection.model_id.strip() or DEFAULT_VOICE_CHANGER_MODEL_ID

    def _initial_noise_enabled(self) -> bool:
        if self._initial_selection is None:
            return True
        return self._initial_selection.remove_background_noise

    def _initial_output_format(self) -> str:
        if self._initial_selection is None:
            return DEFAULT_VOICE_CHANGER_OUTPUT_FORMAT
        value = self._initial_selection.output_format.strip()
        return value or DEFAULT_VOICE_CHANGER_OUTPUT_FORMAT

    def _refresh_confirm_state(self) -> None:
        try:
            confirm = self.query_one("#voice-changer-selector-confirm", Button)
        except NoMatches:
            return
        confirm.disabled = not (self._voices_loaded and self._models_loaded)

    def _set_status(self, message: str, *, error: bool = False) -> None:
        try:
            status = self.query_one("#voice-changer-selector-status", Static)
        except NoMatches:
            logger.debug("VoiceChangerSelector: status ignorado porque el modal ya no existe")
            return
        status.update(f"[red]{message}[/red]" if error else message)
        timeout = _LONG_NOTIFICATION_TIMEOUT if error else _NOTIFICATION_TIMEOUT
        self.notify(
            message,
            severity="error" if error else "information",
            timeout=timeout,
        )
