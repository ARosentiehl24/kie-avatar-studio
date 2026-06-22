from __future__ import annotations

from collections.abc import Callable
from typing import Any

from textual.widgets import Input, Select

from ...domain.errors import VoiceSettingsValidationError
from ...domain.models import VoiceChangerSettings, VoiceSettings
from ._voice_settings_form import build_voice_settings

QueryOne = Callable[..., Any]  # Any: callable sobrecargado de Textual `query_one`.
StatusSetter = Callable[[str], None]
FormValues = tuple[str, str, str, str]


def read_form_values(
    query_one: QueryOne,
    set_error: StatusSetter,
    *,
    loading: str,
    disabled: str,
) -> FormValues | None:
    voice_value = query_one("#voice-changer-selector-select", Select).value
    model_value = query_one("#voice-changer-selector-model", Select).value
    noise_value = query_one("#voice-changer-selector-noise", Select).value
    format_value = query_one("#voice-changer-selector-format", Select).value
    if not isinstance(voice_value, str):
        set_error("elegí una voz válida")
        return None
    if voice_value == disabled:
        return voice_value, "", "", ""
    if not isinstance(model_value, str) or model_value == loading:
        set_error("elegí un modelo válido")
        return None
    if not isinstance(noise_value, str):
        set_error("elegí la opción de ruido")
        return None
    if not isinstance(format_value, str):
        set_error("elegí un formato válido")
        return None
    return voice_value, model_value, noise_value, format_value


def build_selection(
    initial_selection: VoiceChangerSettings | None,
    values: FormValues,
    *,
    noise_on: str,
    voice_settings: VoiceSettings | None,
) -> VoiceChangerSettings:
    voice_value, model_value, noise_value, format_value = values
    selection = (
        initial_selection.model_copy(deep=True)
        if initial_selection is not None
        else VoiceChangerSettings(voice_id=voice_value)
    )
    selection.voice_id = voice_value
    selection.model_id = model_value
    selection.remove_background_noise = noise_value == noise_on
    selection.output_format = format_value
    selection.voice_settings = voice_settings
    return selection


def collect_voice_settings(query_one: QueryOne) -> VoiceSettings | None:
    return build_voice_settings(
        stability=_parse_optional_float(query_one, "voice-changer-stability"),
        similarity_boost=_parse_optional_float(query_one, "voice-changer-similarity"),
        style=_parse_optional_float(query_one, "voice-changer-style"),
        speed=_parse_optional_float(query_one, "voice-changer-speed"),
        language_code=None,
    )


def selected_voice_id(query_one: QueryOne, *, loading: str, disabled: str) -> str | None:
    select = query_one("#voice-changer-selector-select", Select)
    if not isinstance(select, Select):
        return None
    value = select.value
    if not isinstance(value, str) or value in {loading, disabled}:
        return None
    return value


def initial_voice_setting(selection: VoiceChangerSettings | None, field: str) -> str:
    if selection is None or selection.voice_settings is None:
        return ""
    value = getattr(selection.voice_settings, field, None)
    return "" if value is None else str(value)


def _parse_optional_float(query_one: QueryOne, input_id: str) -> float | None:
    input_widget = query_one(f"#{input_id}", Input)
    if not isinstance(input_widget, Input):
        raise VoiceSettingsValidationError(f"{input_id} no existe en el formulario")
    raw = input_widget.value.strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise VoiceSettingsValidationError(f"{input_id} debe ser numérico") from exc
