"""Modal `Configurar workflow`: pre-llena voice_preset.

El JSON puede traer `voice_preset` y `audio_language` pre-cargados. El
modal solo expone `voice_preset` (el `language_code` se configura DENTRO
del preset desde `PresetFormScreen`, así no se duplica el setting). Si
el JSON trae `audio_language`, se respeta tal cual al ejecutar.

### Voice preset

A diferencia de lo que el usuario podría intuir, el campo `voice_preset`
del JSON NO es un voice_id literal de ElevenLabs: es el identificador
(slug del label) de un `VoicePreset` registrado en la pantalla
`Presets`. Para que sea evidente, este modal muestra un `Select` con
todos los presets disponibles y un botón "Crear nuevo preset" que abre
`PresetFormScreen` inline — el preset queda creado en disco y
seleccionado automáticamente sin salir del flow del workflow.

El JSON acepta tanto el `id` (slug) como el `label` (nombre humano) del
preset; la resolución la hace `WorkflowController._resolve_voice_preset`.

Cuando se confirma, llama al callback `on_confirm(voice_preset_id,
None)` y se cierra. El caller dispara el enqueue (o, en el flow real,
abre `WorkflowSummaryScreen` para confirmación final).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import ClassVar, Final

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Select, Static

from ...app_layer.audio_player import AudioPlayer
from ...app_layer.presets_controller import VoicePresetsController
from ...domain.errors import VoicePresetValidationError
from ...domain.models import (
    ModelCreation,
    ModelCreationMethod,
    VoicePreset,
    WorkflowEntry,
    WorkflowPreSettings,
)
from .._icons import ERROR, OK
from .preset_form import PresetFormResult, PresetFormScreen

_NOTIFICATION_TIMEOUT: Final[int] = 4
_LONG_NOTIFICATION_TIMEOUT: Final[int] = 6

# Valor especial del Select que representa "sin preset, usar default voice".
_NO_PRESET_SENTINEL: Final[str] = "__no_preset__"


ConfirmCallback = Callable[[str | None, str | None], Awaitable[None]]


class ConfigureWorkflowScreen(ModalScreen[None]):
    """Modal de pre-configuración antes de encolar el workflow."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "dismiss", "Cancelar"),
    ]

    def __init__(
        self,
        *,
        entry: WorkflowEntry,
        presets_controller: VoicePresetsController,
        audio_player: AudioPlayer,
        on_confirm: ConfirmCallback,
    ) -> None:
        super().__init__()
        self._entry = entry
        self._presets_controller = presets_controller
        self._audio_player = audio_player
        self._on_confirm = on_confirm
        self._presets: list[VoicePreset] = []
        pre_payload = (entry.workflow_payload or {}).get("pre_settings", {})
        try:
            self._initial = WorkflowPreSettings.model_validate(pre_payload)
        except Exception:
            self._initial = WorkflowPreSettings(model_creation=_fallback_model_creation())

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="configure-workflow-box"):
            yield Static(
                f"[b]Configurar workflow:[/b] {self._entry.name}",
                id="configure-workflow-title",
            )
            yield Static(
                "[dim]Estos parámetros se aplican a TODOS los steps del workflow. "
                "Los del JSON aparecen pre-cargados; podés cambiarlos antes de ejecutar.[/dim]",
                id="configure-workflow-subtitle",
            )
            yield Static("[b]Voice preset (registrado en pantalla Presets):[/b]")
            with Horizontal(id="configure-preset-row"):
                yield Select[str](
                    [(self._render_loading_option(), _NO_PRESET_SENTINEL)],
                    allow_blank=False,
                    id="configure-voice-preset-select",
                )
                yield Button(
                    "Crear nuevo preset",
                    id="configure-create-preset",
                    classes="btn-info",
                )
            yield Static(
                "[dim]Si no encontrás el preset que buscás, presioná "
                "'Crear nuevo preset' para registrarlo sin salir de este modal. "
                "El idioma TTS (language_code) se configura DENTRO del preset, no acá.[/dim]",
                id="configure-preset-hint",
            )
            yield Static(
                "[b]Atención:[/b] al continuar verás un resumen final con el "
                "desglose de operaciones Kie que se van a consumir.",
                id="configure-warning",
            )
            with Horizontal(classes="actions-row actions-row-keys"):
                yield Button("Continuar al resumen", id="configure-confirm", variant="primary")
                yield Button("Cancelar", id="configure-cancel", classes="btn-info")
            yield Static("", id="configure-status-bar")
        yield Footer()

    async def on_mount(self) -> None:
        await self._refresh_presets_select()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "configure-cancel":
            self.dismiss()
            return
        if button_id == "configure-confirm":
            await self._handle_confirm()
            return
        if button_id == "configure-create-preset":
            self._handle_create_preset_clicked()

    # --- preset select wiring -----------------------------------------

    async def _refresh_presets_select(self, *, select_preset_id: str | None = None) -> None:
        """(Re)carga el Select desde el store. Selecciona el preset dado o el del JSON."""
        try:
            self._presets = await self._presets_controller.list_all()
        except Exception as exc:
            self._set_status(f"{ERROR} no pude listar los presets: {exc}", error=True)
            return
        select = self.query_one("#configure-voice-preset-select", Select)
        options: list[tuple[str, str]] = [
            (self._render_no_preset_option(), _NO_PRESET_SENTINEL),
        ]
        options.extend((self._render_preset_option(preset), preset.id) for preset in self._presets)
        select.set_options(options)
        select.value = self._resolve_initial_value(select_preset_id)

    def _resolve_initial_value(self, override_id: str | None) -> str:
        """Decide qué opción del Select dejar activa al refrescar.

        Precedencia: override explícito (preset recién creado) >
        valor actual del Select (mantener si todavía existe) >
        match contra el `voice_preset` del JSON >
        sentinela "sin preset".
        """
        if override_id is not None and self._preset_exists(override_id):
            return override_id
        current = self._current_select_value()
        if current is not None and current != _NO_PRESET_SENTINEL and self._preset_exists(current):
            return current
        json_hint = self._initial.voice_preset_id
        if json_hint:
            resolved = self._resolve_by_id_or_label(json_hint)
            if resolved is not None:
                return resolved.id
        return _NO_PRESET_SENTINEL

    def _current_select_value(self) -> str | None:
        try:
            select = self.query_one("#configure-voice-preset-select", Select)
        except Exception:
            return None
        value = select.value
        return value if isinstance(value, str) else None

    def _preset_exists(self, preset_id: str) -> bool:
        return any(p.id == preset_id for p in self._presets)

    def _resolve_by_id_or_label(self, value: str) -> VoicePreset | None:
        """Mismo algoritmo que el controller: busca por id, después por label."""
        target = value.strip()
        if not target:
            return None
        for preset in self._presets:
            if preset.id == target:
                return preset
        target_lower = target.lower()
        for preset in self._presets:
            if preset.label.lower() == target_lower:
                return preset
        return None

    # --- handlers -----------------------------------------------------

    async def _handle_confirm(self) -> None:
        preset_value = self._current_select_value()
        voice_preset_id = None if preset_value in (None, _NO_PRESET_SENTINEL) else preset_value
        # `audio_language` ya NO se pide en este modal: el `language_code`
        # del preset (configurable en `PresetFormScreen`) tiene prioridad.
        # Si el JSON trae `audio_language`, se respeta tal cual (backdoor).
        try:
            await self._on_confirm(voice_preset_id, None)
        except Exception as exc:
            self._set_status(f"{ERROR} {exc}", error=True)
            return
        self._set_status(f"{OK} configuración aplicada")
        self.dismiss()

    def _handle_create_preset_clicked(self) -> None:
        # Push del PresetFormScreen modal en modo create. Pasamos el
        # audio_player (vía AutomationScreen) para que el modal pueda
        # reproducir el preview de las voces.
        self.app.push_screen(
            PresetFormScreen(existing=None, audio_player=self._audio_player),
            self._on_preset_form_dismissed,
        )

    def _on_preset_form_dismissed(self, result: PresetFormResult | None) -> None:
        if result is None:
            return
        # PresetFormScreen es síncrono al dismiss; persistimos en background
        # y refrescamos el Select cuando termina.
        self.app.run_worker(self._persist_new_preset(result), exclusive=False)

    async def _persist_new_preset(self, result: PresetFormResult) -> None:
        try:
            preset = await self._presets_controller.create(
                label=result.label,
                voice_id=result.voice_id,
                voice_settings=result.voice_settings,
                description=result.description,
            )
        except VoicePresetValidationError as exc:
            self._set_status(f"{ERROR} no pude crear el preset: {exc}", error=True)
            return
        except Exception as exc:
            self._set_status(f"{ERROR} error inesperado: {exc}", error=True)
            return
        await self._refresh_presets_select(select_preset_id=preset.id)
        self._set_status(f"{OK} preset '{preset.label}' creado y seleccionado")

    # --- option renderers ---------------------------------------------

    @staticmethod
    def _render_loading_option() -> str:
        return "Cargando presets…"

    @staticmethod
    def _render_no_preset_option() -> str:
        return "(sin preset — usar voice default)"

    @staticmethod
    def _render_preset_option(preset: VoicePreset) -> str:
        if preset.label != preset.id:
            return f"{preset.label}  ·  id={preset.id}"
        return preset.label

    # --- status bar ---------------------------------------------------

    def _set_status(self, message: str, *, error: bool = False) -> None:
        try:
            bar = self.query_one("#configure-status-bar", Static)
        except Exception:
            return
        bar.update(f"[red]{message}[/red]" if error else message)
        timeout = _LONG_NOTIFICATION_TIMEOUT if error else _NOTIFICATION_TIMEOUT
        self.notify(
            message,
            severity="error" if error else "information",
            timeout=timeout,
        )


def _fallback_model_creation() -> ModelCreation:
    """Fallback cuando el JSON no trae pre_settings completos."""
    return ModelCreation(method=ModelCreationMethod.PROMPT, prompt="A photorealistic person")


__all__ = ["ConfigureWorkflowScreen"]
