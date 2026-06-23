from __future__ import annotations

from collections.abc import Callable

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Footer, Header, Input, Select, Static


def compose_voice_changer_selector(
    *,
    audio_preview_available: bool,
    initial_noise_enabled: bool,
    output_format_options: list[tuple[str, str]],
    initial_output_format: str,
    initial_voice_setting: Callable[[str], str],
    loading_value: str,
    noise_on_value: str,
    noise_off_value: str,
) -> ComposeResult:
    yield Header(show_clock=False)
    with Vertical(id="voice-changer-selector-box"):
        yield Static(
            "[b]Configurar voice changer (ElevenLabs)[/b]", id="voice-changer-selector-title"
        )
        yield from _selector_body(
            audio_preview_available=audio_preview_available,
            initial_noise_enabled=initial_noise_enabled,
            output_format_options=output_format_options,
            initial_output_format=initial_output_format,
            initial_voice_setting=initial_voice_setting,
            loading_value=loading_value,
            noise_on_value=noise_on_value,
            noise_off_value=noise_off_value,
        )
        yield from _actions()
    yield Footer()


def _selector_body(
    *,
    audio_preview_available: bool,
    initial_noise_enabled: bool,
    output_format_options: list[tuple[str, str]],
    initial_output_format: str,
    initial_voice_setting: Callable[[str], str],
    loading_value: str,
    noise_on_value: str,
    noise_off_value: str,
) -> ComposeResult:
    with VerticalScroll(id="voice-changer-selector-body"):
        yield Static(
            "[dim]Se aplica al audio final del workflow. Podés elegir voz, modelo "
            "STS, remoción de ruido, formato y voice settings opcionales.[/dim]",
            id="voice-changer-selector-subtitle",
        )
        yield from _voice_block(audio_preview_available, loading_value)
        yield from _model_block(loading_value)
        yield from _noise_block(initial_noise_enabled, noise_on_value, noise_off_value)
        yield from _format_block(output_format_options, initial_output_format)
        yield Static("[b]Voice settings opcionales:[/b]")
        yield Static("[dim]Dejá vacío para usar el default de ElevenLabs.[/dim]")
        yield from _voice_setting_inputs(initial_voice_setting)
        yield Static("", id="voice-changer-selector-status")


def _voice_block(audio_preview_available: bool, loading_value: str) -> ComposeResult:
    yield Static("[b]Voz:[/b]")
    yield Input(
        placeholder="Buscar por nombre o voice_id…",
        id="voice-changer-selector-search",
    )
    yield Static("", id="voice-changer-selector-search-status")
    yield Select[str](
        [("Cargando voces…", loading_value)],
        value=loading_value,
        allow_blank=False,
        id="voice-changer-selector-select",
    )
    with Horizontal(id="voice-changer-preview-row"):
        yield Button(
            "Escuchar preview",
            id="voice-changer-selector-preview",
            classes="btn-info",
            disabled=not audio_preview_available,
        )
        yield Button(
            "Detener",
            id="voice-changer-selector-preview-stop",
            classes="btn-warning",
            disabled=not audio_preview_available,
        )


def _model_block(loading_value: str) -> ComposeResult:
    yield Static("[b]Modelo STS:[/b]")
    yield Select[str](
        [("Cargando modelos…", loading_value)],
        value=loading_value,
        allow_blank=False,
        id="voice-changer-selector-model",
    )


def _noise_block(initial: bool, on_value: str, off_value: str) -> ComposeResult:
    yield Static("[b]Remover ruido de fondo:[/b]")
    yield Select[str](
        [("Sí (recomendado)", on_value), ("No", off_value)],
        value=on_value if initial else off_value,
        allow_blank=False,
        id="voice-changer-selector-noise",
    )


def _format_block(options: list[tuple[str, str]], initial: str) -> ComposeResult:
    yield Static("[b]Formato de salida:[/b]")
    yield Select[str](
        options,
        value=initial,
        allow_blank=False,
        id="voice-changer-selector-format",
    )


def _actions() -> ComposeResult:
    with Horizontal(classes="actions-row actions-row-keys"):
        yield Button(
            "Usar selección", id="voice-changer-selector-confirm", variant="primary", disabled=True
        )
        yield Button("Cancelar", id="voice-changer-selector-cancel", variant="default")


def _voice_setting_inputs(initial: Callable[[str], str]) -> ComposeResult:
    yield Static("Estabilidad / stability (0.0 - 1.0)")
    yield Input(placeholder="0.5", value=initial("stability"), id="voice-changer-stability")
    yield Static("Similitud / similarity_boost (0.0 - 1.0)")
    yield Input(
        placeholder="0.75",
        value=initial("similarity_boost"),
        id="voice-changer-similarity",
    )
    yield Static("Estilo / style (0.0 - 1.0)")
    yield Input(placeholder="0", value=initial("style"), id="voice-changer-style")
    yield Static("Velocidad / speed (0.7 - 1.2)")
    yield Input(placeholder="1.0", value=initial("speed"), id="voice-changer-speed")
