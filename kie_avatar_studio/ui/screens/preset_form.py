"""Modal para crear o editar un `VoicePreset`.

Solo dispatch + render (CR-10.1). Reusa el patrón del modal Generate
Audio: header + body scrollable + footer sticky con botones. El
preset se guarda via callback (`on_save`) que el caller le pasa,
así el modal queda independiente del controller concreto.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Final

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Collapsible, Input, Label, Select, Static, TextArea

from ...domain.kie_voice_catalog import BUILTIN_VOICES
from ...domain.models import VoicePreset, VoiceSettings
from ...domain.policies import MAX_SCRIPT_CHARS

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

    def __init__(self, existing: VoicePreset | None = None) -> None:
        super().__init__()
        self._existing = existing
        self._is_edit = existing is not None

    def compose(self) -> ComposeResult:
        title = _FORM_TITLE_EDIT if self._is_edit else _FORM_TITLE_NEW
        with Vertical(id="preset-form-dialog"):
            with VerticalScroll(id="preset-form-body"):
                yield Static(title, id="preset-form-title")

                yield Label("Nombre del preset (ej. 'narrador calmo')")
                yield Input(
                    placeholder="narrador calmo",
                    id="preset-label",
                    value=self._existing.label if self._existing else "",
                )

                yield Label("Voz (catálogo built-in de Kie — 67 voces)")
                yield Select(
                    options=[(voice.display_name, voice.voice_id) for voice in BUILTIN_VOICES],
                    value=self._initial_voice_id(),
                    allow_blank=False,
                    id="preset-voice",
                )

                yield Label(f"Descripción opcional (máx {_DESCRIPTION_MAX} chars)")
                yield TextArea(
                    self._existing.description or "" if self._existing else "",
                    id="preset-description",
                    language=None,
                )

                with Collapsible(title="Avanzado — voice settings", id="preset-advanced"):
                    yield Label("stability (0.0 - 1.0, vacío = default 0.5)")
                    yield Input(
                        placeholder="0.5",
                        id="preset-stability",
                        value=self._initial("stability"),
                    )
                    yield Label("similarity_boost (0.0 - 1.0, vacío = default 0.75)")
                    yield Input(
                        placeholder="0.75",
                        id="preset-similarity",
                        value=self._initial("similarity_boost"),
                    )
                    yield Label("style (0.0 - 1.0, vacío = default 0)")
                    yield Input(placeholder="0", id="preset-style", value=self._initial("style"))
                    yield Label("speed (0.7 - 1.2, vacío = default 1.0)")
                    yield Input(
                        placeholder="1.0",
                        id="preset-speed",
                        value=self._initial("speed"),
                    )
                    yield Label("language_code ISO 639-1 (vacío = auto; solo turbo/flash v2.5)")
                    yield Input(
                        placeholder="es",
                        id="preset-language",
                        value=self._initial_language_code(),
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

    def action_cancel(self) -> None:
        self.dismiss(None)

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
        settings = self._collect_voice_settings()
        # Si el usuario escribió algo raro en los settings avanzados,
        # `_collect_voice_settings` puede devolver None; capturamos
        # ValueError adentro para mostrar en el form.
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
            return ""
        return self._existing.voice_settings.language_code

    def _collect_voice_settings(self) -> VoiceSettings | None:
        """Parsea los 5 inputs avanzados a VoiceSettings o None.

        Si todos vacíos → None (Kie aplica defaults). Si alguno tiene
        valor, lo parsea con tolerancia: errores de rango caen al
        Field validator de Pydantic; el modal NO valida acá para no
        duplicar la lógica que ya está en domain.policies.
        """
        stability = self._parse_float("preset-stability")
        similarity = self._parse_float("preset-similarity")
        style = self._parse_float("preset-style")
        speed = self._parse_float("preset-speed")
        language = self.query_one("#preset-language", Input).value.strip() or None
        if all(v is None for v in (stability, similarity, style, speed, language)):
            return None
        try:
            return VoiceSettings(
                stability=stability,
                similarity_boost=similarity,
                style=style,
                speed=speed,
                language_code=language,
            )
        except ValueError:
            # Devolvemos None silencioso: el caller (controller) re-valida
            # via validate_voice_settings y muestra el error al usuario.
            return None

    def _parse_float(self, input_id: str) -> float | None:
        raw = self.query_one(f"#{input_id}", Input).value.strip()
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None


# Helper no usado en runtime, solo para que ruff no se queje del import.
_ = MAX_SCRIPT_CHARS
