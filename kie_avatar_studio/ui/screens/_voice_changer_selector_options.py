from __future__ import annotations

from dataclasses import dataclass

from ...domain.ports import ExternalJsonObject


@dataclass(frozen=True, slots=True)
class VoiceOptionsResult:
    options: list[tuple[str, str]]
    preview_urls: dict[str, str]
    visible_count: int


def build_voice_options(
    raw_voices: list[ExternalJsonObject], *, current_voice_id: str | None, disabled_value: str
) -> VoiceOptionsResult:
    options: list[tuple[str, str]] = [("Sin voice changer", disabled_value)]
    preview_urls: dict[str, str] = {}
    seen_voice_ids: set[str] = set()
    visible_count = 0
    for raw_voice in raw_voices:
        voice_id = raw_voice.get("voice_id")
        name = raw_voice.get("name")
        if not isinstance(voice_id, str) or not voice_id.strip():
            continue
        voice_id = voice_id.strip()
        if voice_id in seen_voice_ids:
            continue
        seen_voice_ids.add(voice_id)
        preview_url = raw_voice.get("preview_url")
        if isinstance(preview_url, str) and preview_url.strip():
            preview_urls[voice_id] = preview_url.strip()
        label = name.strip() if isinstance(name, str) and name.strip() else voice_id
        options.append((f"{label}  ·  {voice_id}", voice_id))
        visible_count += 1
    if current_voice_id and all(value != current_voice_id for _, value in options):
        options.insert(1, (f"Actual (no listada)  ·  {current_voice_id}", current_voice_id))
    return VoiceOptionsResult(options, preview_urls, visible_count)


def build_model_options(
    raw_models: list[ExternalJsonObject],
    *,
    current_model_id: str,
    default_model_id: str,
) -> tuple[list[tuple[str, str]], int]:
    options: list[tuple[str, str]] = []
    seen_model_ids: set[str] = set()
    visible_count = 0
    for raw_model in raw_models:
        model_id = raw_model.get("model_id")
        if not isinstance(model_id, str) or not model_id.strip():
            continue
        model_id = model_id.strip()
        if model_id in seen_model_ids or not is_sts_model(raw_model):
            continue
        seen_model_ids.add(model_id)
        name = raw_model.get("name")
        label = name.strip() if isinstance(name, str) and name.strip() else model_id
        options.append((f"{label}  ·  {model_id}", model_id))
        visible_count += 1
    if not options:
        options.append((f"Default  ·  {default_model_id}", default_model_id))
    if current_model_id and all(value != current_model_id for _, value in options):
        options.insert(0, (f"Actual (no listado)  ·  {current_model_id}", current_model_id))
    return options, visible_count


def is_sts_model(raw_model: ExternalJsonObject) -> bool:
    can_voice_conversion = raw_model.get("can_do_voice_conversion")
    if isinstance(can_voice_conversion, bool):
        return can_voice_conversion
    model_id = raw_model.get("model_id")
    name = raw_model.get("name")
    haystack = " ".join(part.lower() for part in (model_id, name) if isinstance(part, str) and part)
    return "sts" in haystack or "speech-to-speech" in haystack or "voice conversion" in haystack
