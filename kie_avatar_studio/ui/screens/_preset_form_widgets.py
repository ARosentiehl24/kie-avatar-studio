"""Secciones reutilizables del modal de preset de voz."""

from __future__ import annotations

from collections.abc import Callable

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, Collapsible, Input, Label, Select, TextArea

from ...domain.kie_voice_catalog import BUILTIN_VOICES
from ._voice_language_options import voice_language_options


def compose_name_field(label: str) -> ComposeResult:
    yield Label("Nombre del preset (ej. 'narrador calmo')")
    yield Input(placeholder="narrador calmo", id="preset-label", value=label)


def compose_voice_selector(initial_voice_id: str, *, with_preview: bool) -> ComposeResult:
    yield Label(f"Voz (catálogo built-in de Kie — {len(BUILTIN_VOICES)} voces)")
    with Horizontal(id="preset-voice-row"):
        yield Select(
            options=[(voice.display_name, voice.voice_id) for voice in BUILTIN_VOICES],
            value=initial_voice_id,
            allow_blank=False,
            id="preset-voice",
        )
        if with_preview:
            yield Button("Preview", id="preset-preview", classes="btn-info")
            yield Button("Detener", id="preset-preview-stop", classes="btn-warning")


def compose_description_field(description: str, *, max_chars: int) -> ComposeResult:
    yield Label(f"Descripción opcional (máx {max_chars} chars)")
    yield TextArea(description, id="preset-description", language=None)


def compose_advanced_settings(
    initial: Callable[[str], str],
    initial_language_code: str,
) -> ComposeResult:
    with Collapsible(title="Avanzado — voice settings", id="preset-advanced"):
        yield Label("stability (0.0 - 1.0, vacío = default 0.5)")
        yield Input(placeholder="0.5", id="preset-stability", value=initial("stability"))
        yield Label("similarity_boost (0.0 - 1.0, vacío = default 0.75)")
        yield Input(
            placeholder="0.75",
            id="preset-similarity",
            value=initial("similarity_boost"),
        )
        yield Label("style (0.0 - 1.0, vacío = default 0)")
        yield Input(placeholder="0", id="preset-style", value=initial("style"))
        yield Label("speed (0.7 - 1.2, vacío = default 1.0)")
        yield Input(placeholder="1.0", id="preset-speed", value=initial("speed"))
        yield Label("Idioma (language_code BCP-47 usado por Kie/ElevenLabs)")
        yield Select(
            options=voice_language_options(initial_language_code),
            value=initial_language_code,
            allow_blank=False,
            id="preset-language",
        )
