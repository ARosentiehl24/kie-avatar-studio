"""Modal para crear o editar un `VoicePreset`.

Renderiza un form independiente del controller y permite escuchar previews
de voces built-in cuando recibe un `AudioPlayer` inyectado.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Final

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static, TextArea

from ...app_layer.audio_player import AudioPlayer
from ...domain.errors import UrlValidationError, VoiceSettingsValidationError
from ...domain.kie_voice_catalog import BUILTIN_VOICES, get_builtin_voice
from ...domain.models import VoicePreset, VoiceSettings
from ._preset_form_widgets import (
    compose_advanced_settings,
    compose_description_field,
    compose_name_field,
    compose_voice_selector,
)
from ._voice_language_options import (
    LANGUAGE_AUTO_SENTINEL,
    selected_language_code,
)
from ._voice_settings_form import build_voice_settings

_FORM_TITLE_NEW: Final[str] = "Nuevo preset de voz"
_FORM_TITLE_EDIT: Final[str] = "Editar preset de voz"
_DESCRIPTION_MAX: Final[int] = 200


@dataclass(frozen=True, slots=True)
class PresetFormResult:
    """Payload devuelto cuando el usuario confirma el form.

    `id_to_update` está poblado solo en modo edición (None en create).
    El caller decide entre `controller.create` y `controller.update`
    según ese campo.
    """

    id_to_update: str | None
    label: str
    voice_id: str
    voice_settings: VoiceSettings | None
    description: str | None


class PresetFormScreen(ModalScreen[PresetFormResult | None]):
    """Modal para crear o editar un VoicePreset."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "cancel", "Cancelar", show=False),
    ]

    def __init__(
        self,
        existing: VoicePreset | None = None,
        *,
        audio_player: AudioPlayer | None = None,
    ) -> None:
        super().__init__()
        self._existing = existing
        self._is_edit = existing is not None
        self._audio_player = audio_player

    def compose(self) -> ComposeResult:
        title = _FORM_TITLE_EDIT if self._is_edit else _FORM_TITLE_NEW
        with Vertical(id="preset-form-dialog"):
            with VerticalScroll(id="preset-form-body"):
                yield Static(title, id="preset-form-title")
                yield from compose_name_field(self._existing.label if self._existing else "")
                yield from compose_voice_selector(
                    self._initial_voice_id(),
                    with_preview=self._audio_player is not None,
                )
                yield from compose_description_field(
                    self._existing.description or "" if self._existing else "",
                    max_chars=_DESCRIPTION_MAX,
                )
                yield from compose_advanced_settings(
                    self._initial,
                    self._initial_language_code(),
                )
                yield Static("", id="preset-form-error")
            with Horizontal(id="preset-form-footer"):
                yield Button("Cancelar", id="cancel", variant="default")
                save_label = "Guardar cambios" if self._is_edit else "Crear preset"
                yield Button(save_label, id="save", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#preset-label", Input).focus()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "cancel":
            self.action_cancel()
        elif bid == "save":
            await self._on_save()
        elif bid == "preset-preview":
            self._handle_preview()
        elif bid == "preset-preview-stop":
            self._handle_preview_stop()

    def action_cancel(self) -> None:
        # Stop del preview antes de cerrar: el usuario que cancela espera
        # silencio inmediato.
        self._stop_preview_async()
        self.dismiss(None)

    # --- preview handlers --------------------------------------------------

    def _handle_preview(self) -> None:
        """Reproduce el preview de la voz seleccionada (auto-cancela el anterior)."""
        if self._audio_player is None:
            self._set_error("preview no disponible (audio_player no inyectado)")
            return
        voice_id = self._selected_voice_id()
        if voice_id is None:
            self._set_error("Seleccioná una voz primero")
            return
        voice = get_builtin_voice(voice_id)
        if voice is None:
            self._set_error(f"voice_id {voice_id!r} no está en el catálogo built-in")
            return
        if not voice.preview_url:
            self._set_error(f"la voz '{voice.label}' no tiene preview disponible")
            return
        self.app.run_worker(self._open_preview(voice.preview_url), exclusive=False)

    def _handle_preview_stop(self) -> None:
        """Detiene la reproducción del preview en curso. Idempotente."""
        self._stop_preview_async()

    def _stop_preview_async(self) -> None:
        if self._audio_player is None:
            return
        self.app.run_worker(self._audio_player.stop(), exclusive=False)

    async def _open_preview(self, url: str) -> None:
        if self._audio_player is None:
            return
        try:
            await self._audio_player.play_voice_preview(url)
        except (OSError, UrlValidationError) as exc:
            self._set_error(f"no pude reproducir el preview: {exc}")

    def _selected_voice_id(self) -> str | None:
        select = self.query_one("#preset-voice", Select)
        value = select.value
        if value is Select.BLANK or not isinstance(value, str):
            return None
        return value

    def _set_error(self, message: str) -> None:
        error = self.query_one("#preset-form-error", Static)
        error.update(f"[red]{message}[/red]")

    # --- internos ---------------------------------------------------------

    async def _on_save(self) -> None:
        error = self.query_one("#preset-form-error", Static)
        label = self.query_one("#preset-label", Input).value.strip()
        if not label:
            error.update("[red]El nombre del preset no puede estar vacío.[/red]")
            return
        voice_id = self.query_one("#preset-voice", Select).value
        if not isinstance(voice_id, str) or not voice_id:
            error.update("[red]Elegí una voz.[/red]")
            return
        description = self.query_one("#preset-description", TextArea).text.strip()
        if len(description) > _DESCRIPTION_MAX:
            error.update(f"[red]La descripción supera {_DESCRIPTION_MAX} caracteres.[/red]")
            return
        try:
            settings = self._collect_voice_settings()
        except VoiceSettingsValidationError as exc:
            error.update(f"[red]{exc}[/red]")
            return
        # Stop del preview antes de cerrar: si el usuario guarda mientras
        # un audio sonaba, esperaría silencio inmediato.
        self._stop_preview_async()
        self.dismiss(
            PresetFormResult(
                id_to_update=self._existing.id if self._existing else None,
                label=label,
                voice_id=voice_id,
                voice_settings=settings,
                description=description or None,
            )
        )

    def _initial(self, field: str) -> str:
        """Pre-carga un campo numérico del preset existente (modo edición)."""
        if self._existing is None or self._existing.voice_settings is None:
            return ""
        value = getattr(self._existing.voice_settings, field, None)
        return "" if value is None else str(value)

    def _initial_voice_id(self) -> str:
        if self._existing is not None:
            return self._existing.voice_id
        return BUILTIN_VOICES[0].voice_id

    def _initial_language_code(self) -> str:
        if (
            self._existing is None
            or self._existing.voice_settings is None
            or self._existing.voice_settings.language_code is None
        ):
            return LANGUAGE_AUTO_SENTINEL
        return self._existing.voice_settings.language_code

    def _collect_voice_settings(self) -> VoiceSettings | None:
        """Parsea los 5 inputs avanzados a VoiceSettings o None.

        Si todos vacíos → None (Kie aplica defaults). Si alguno tiene valor,
        delega el armado y los errores de rango al helper compartido.
        """
        stability = self._parse_float("preset-stability")
        similarity = self._parse_float("preset-similarity")
        style = self._parse_float("preset-style")
        speed = self._parse_float("preset-speed")
        language = selected_language_code(self.query_one("#preset-language", Select).value)
        return build_voice_settings(
            stability=stability,
            similarity_boost=similarity,
            style=style,
            speed=speed,
            language_code=language,
        )

    def _parse_float(self, input_id: str) -> float | None:
        raw = self.query_one(f"#{input_id}", Input).value.strip()
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError as exc:
            raise VoiceSettingsValidationError(f"{input_id} debe ser numérico") from exc
