"""Pantalla `Presets`: CRUD de voice presets (combinaciones reusables).

Solo dispatch + render (CR-10.1). Tabla con todos los presets del
usuario + acciones para crear nuevo / editar / eliminar.

Los presets se persisten file-based en `Settings.presets_dir/voices/`
(decisión deliberada — ver `infra/presets_store.py`).

V1: solo CRUD standalone. La integración con los modales Generate
Audio y New Video (precargar un preset al elegirlo de un Select)
queda planeada para un commit posterior.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import ClassVar, Final

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Static

from ...app_layer.audio_player import AudioPlayer
from ...app_layer.presets_controller import VoicePresetsController
from ...domain.errors import VoicePresetNotFoundError, VoicePresetValidationError
from ...domain.kie_voice_catalog import get_builtin_voice
from ...domain.models import VoicePreset
from .._icons import ERROR, OK
from .._table_helpers import get_selected_row_key, select_row_by_key
from .._text_format import truncate
from .preset_form import PresetFormResult, PresetFormScreen

_NOTIFICATION_TIMEOUT: Final[int] = 4
_LONG_NOTIFICATION_TIMEOUT: Final[int] = 6
_DESCRIPTION_PREVIEW_LEN: Final[int] = 40

_TABLE_COLUMNS: Final[tuple[str, ...]] = (
    "Nombre",
    "Voz",
    "Settings",
    "Descripción",
    "Modificado",
)


class PresetsScreen(Screen[None]):
    """CRUD de voice presets."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "go_back", "Volver"),
    ]

    def __init__(
        self,
        controller: VoicePresetsController,
        *,
        audio_player: AudioPlayer,
    ) -> None:
        super().__init__()
        self._controller = controller
        self._audio_player = audio_player

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="presets-box"):
            yield Static("[b]Presets de voz — combinaciones reusables[/b]", id="presets-title")
            yield Static(
                "[dim]Guardá combinaciones voice_id + settings con un nombre legible "
                "para reusarlas rápido en futuras generaciones de audio o video.[/dim]",
                id="presets-subtitle",
            )
            table: DataTable[str] = DataTable(
                id="presets-table", cursor_type="row", zebra_stripes=True
            )
            for column in _TABLE_COLUMNS:
                table.add_column(column, key=column)
            yield table
            with Horizontal(classes="actions-row actions-row-keys"):
                yield Button("Nuevo preset", id="preset-new", variant="primary")
                yield Button("Editar", id="preset-edit", classes="btn-info")
                yield Button("Eliminar", id="preset-delete", variant="error")
            yield Static(
                "[dim]Los presets se guardan como JSON editable en "
                "presets/voices/. Podés editarlos a mano si querés.[/dim]",
                id="presets-hint",
            )
            yield Static("", id="status-bar")
        yield Footer()

    async def on_mount(self) -> None:
        await self._refresh_table()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        handler = _BUTTON_HANDLERS.get(button_id)
        if handler is None:
            return
        await handler(self)

    def action_go_back(self) -> None:
        self.app.pop_screen()

    # --- handlers ---------------------------------------------------------

    async def _handle_new(self) -> None:
        self.app.push_screen(
            PresetFormScreen(existing=None, audio_player=self._audio_player),
            self._on_form_dismissed,
        )

    async def _handle_edit(self) -> None:
        preset = await self._selected_preset()
        if preset is None:
            return
        self.app.push_screen(
            PresetFormScreen(existing=preset, audio_player=self._audio_player),
            self._on_form_dismissed,
        )

    async def _handle_delete(self) -> None:
        preset = await self._selected_preset()
        if preset is None:
            return
        try:
            await self._controller.delete(preset.id)
        except VoicePresetNotFoundError:
            self._set_status("Ese preset ya no existe", error=True)
            return
        self._set_status(f"{OK} preset '{preset.label}' eliminado")
        await self._refresh_table()

    def _on_form_dismissed(self, result: PresetFormResult | None) -> None:
        if result is None:
            return
        self.app.run_worker(self._persist_form(result), exclusive=False)

    async def _persist_form(self, result: PresetFormResult) -> None:
        try:
            if result.id_to_update is not None:
                preset = await self._controller.update(
                    result.id_to_update,
                    label=result.label,
                    voice_id=result.voice_id,
                    voice_settings=result.voice_settings,
                    description=result.description,
                )
                msg = f"{OK} preset '{preset.label}' actualizado"
            else:
                preset = await self._controller.create(
                    label=result.label,
                    voice_id=result.voice_id,
                    voice_settings=result.voice_settings,
                    description=result.description,
                )
                msg = f"{OK} preset '{preset.label}' creado"
        except VoicePresetValidationError as exc:
            self._set_status(f"{ERROR} {exc}", error=True)
            return
        except VoicePresetNotFoundError as exc:
            self._set_status(f"{ERROR} {exc}", error=True)
            return
        self._set_status(msg)
        await self._refresh_table()

    # --- helpers ----------------------------------------------------------

    async def _refresh_table(self) -> None:
        presets = await self._controller.list_all()
        table = self.query_one("#presets-table", DataTable)
        previous_id = get_selected_row_key(table)
        table.clear()
        for preset in presets:
            table.add_row(
                preset.label,
                _format_voice(preset.voice_id),
                _format_settings_summary(preset),
                truncate(preset.description or "—", _DESCRIPTION_PREVIEW_LEN),
                preset.updated_at.strftime("%Y-%m-%d %H:%M"),
                key=preset.id,
            )
        if previous_id is not None:
            select_row_by_key(table, previous_id)

    async def _selected_preset(self) -> VoicePreset | None:
        table = self.query_one("#presets-table", DataTable)
        preset_id = get_selected_row_key(table)
        if preset_id is None:
            self._set_status("Seleccioná un preset en la tabla primero", error=True)
            return None
        preset = await self._controller.get(preset_id)
        if preset is None:
            self._set_status("Ese preset ya no existe", error=True)
            return None
        return preset

    def _set_status(self, message: str, *, error: bool = False) -> None:
        try:
            bar = self.query_one("#status-bar", Static)
        except Exception:
            return
        bar.update(f"[red]{message}[/red]" if error else message)
        timeout = _LONG_NOTIFICATION_TIMEOUT if error else _NOTIFICATION_TIMEOUT
        self.notify(message, severity="error" if error else "information", timeout=timeout)


def _format_voice(voice_id: str) -> str:
    """Nombre legible del catálogo o el id raw si no se encuentra."""
    voice = get_builtin_voice(voice_id)
    return voice.label if voice is not None else truncate(voice_id, 18)


def _format_settings_summary(preset: VoicePreset) -> str:
    """Render compacto de los voice_settings del preset."""
    if preset.voice_settings is None:
        return "[dim]defaults Kie[/dim]"
    settings = preset.voice_settings
    parts: list[str] = []
    if settings.stability is not None:
        parts.append(f"sta={settings.stability}")
    if settings.similarity_boost is not None:
        parts.append(f"sim={settings.similarity_boost}")
    if settings.style is not None:
        parts.append(f"sty={settings.style}")
    if settings.speed is not None:
        parts.append(f"spd={settings.speed}")
    if settings.language_code is not None:
        parts.append(f"lang={settings.language_code}")
    return " · ".join(parts) if parts else "[dim]defaults Kie[/dim]"


_BUTTON_HANDLERS: dict[str, Callable[[PresetsScreen], Awaitable[None]]] = {
    "preset-new": PresetsScreen._handle_new,
    "preset-edit": PresetsScreen._handle_edit,
    "preset-delete": PresetsScreen._handle_delete,
}
