"""Modal `Configurar workflow`: pre-llena voice_preset + audio_language.

El JSON puede traer estos campos pre-cargados (los del usuario los
respeta) pero la pantalla permite editarlos antes de ejecutar para que
el usuario no tenga que editar el JSON cada vez que cambia de voz.

Cuando se confirma, llama al callback `on_confirm(voice_preset_id,
audio_language)` y se cierra. El caller dispara el enqueue.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import ClassVar, Final

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Static

from ...domain.models import (
    ModelCreation,
    ModelCreationMethod,
    WorkflowEntry,
    WorkflowPreSettings,
)
from .._icons import ERROR, OK

_NOTIFICATION_TIMEOUT: Final[int] = 4


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
        on_confirm: ConfirmCallback,
    ) -> None:
        super().__init__()
        self._entry = entry
        self._on_confirm = on_confirm
        pre_payload = (entry.workflow_payload or {}).get("pre_settings", {})
        try:
            self._initial = WorkflowPreSettings.model_validate(pre_payload)
        except Exception:
            self._initial = WorkflowPreSettings(
                model_creation=_fallback_model_creation()
            )

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
            yield Static("[b]Voice preset (ID del preset registrado):[/b]")
            yield Input(
                value=self._initial.voice_preset_id or "",
                placeholder="ej: latina_warm_authentic (vacío = voice ID default)",
                id="configure-voice-preset",
            )
            yield Static("[b]Audio language (BCP 47, opcional):[/b]")
            yield Input(
                value=self._initial.audio_language or "",
                placeholder="ej: es-419, pt-BR, en (vacío = modelo multilingual default)",
                id="configure-audio-language",
            )
            yield Static(
                "[dim]Si seteás audio_language se usa el modelo TTS turbo (acepta "
                "language_code); si lo dejás vacío se usa el multilingual default.[/dim]",
                id="configure-hint",
            )
            yield Static(
                "[b]Atención:[/b] al confirmar, este workflow consumirá créditos de Kie. "
                "Verificá el saldo en la pantalla Configuración (C) antes de continuar.",
                id="configure-warning",
            )
            with Horizontal(classes="actions-row actions-row-keys"):
                yield Button("Confirmar y encolar", id="configure-confirm", variant="primary")
                yield Button("Cancelar", id="configure-cancel", classes="btn-info")
            yield Static("", id="configure-status-bar")
        yield Footer()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "configure-cancel":
            self.dismiss()
            return
        if button_id == "configure-confirm":
            await self._handle_confirm()

    async def _handle_confirm(self) -> None:
        voice_preset = self.query_one("#configure-voice-preset", Input).value.strip() or None
        audio_lang = self.query_one("#configure-audio-language", Input).value.strip() or None
        try:
            await self._on_confirm(voice_preset, audio_lang)
        except Exception as exc:
            self._set_status(f"{ERROR} {exc}", error=True)
            return
        self._set_status(f"{OK} workflow encolado")
        self.dismiss()

    def _set_status(self, message: str, *, error: bool = False) -> None:
        try:
            bar = self.query_one("#configure-status-bar", Static)
        except Exception:
            return
        bar.update(f"[red]{message}[/red]" if error else message)
        self.notify(
            message,
            severity="error" if error else "information",
            timeout=_NOTIFICATION_TIMEOUT,
        )


def _fallback_model_creation() -> ModelCreation:
    """Fallback cuando el JSON no trae pre_settings completos."""
    return ModelCreation(method=ModelCreationMethod.PROMPT, prompt="A photorealistic person")
