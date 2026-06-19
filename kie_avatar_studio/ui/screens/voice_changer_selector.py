from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Final, Protocol

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Select, Static

from ...domain.models import VoiceChangerSettings
from .._icons import ERROR, OK

_NOTIFICATION_TIMEOUT: Final[int] = 4
_LONG_NOTIFICATION_TIMEOUT: Final[int] = 6
_LOADING_SENTINEL: Final[str] = "__loading__"
_NO_VOICE_CHANGER_SENTINEL: Final[str] = "__no_voice_changer__"


class ElevenLabsVoicesClient(Protocol):
    """Contrato mínimo para listar voces de ElevenLabs desde la UI."""

    async def list_voices(
        self,
        *,
        voice_type: str | None = None,
        search: str | None = None,
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class VoiceChangerSelectionResult:
    """Resultado explícito del modal; `voice_changer=None` = deshabilitado."""

    voice_changer: VoiceChangerSettings | None


class VoiceChangerSelectorScreen(ModalScreen[VoiceChangerSelectionResult | None]):
    """Modal para elegir la voz del voice changer de ElevenLabs."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "cancel", "Cancelar"),
    ]

    def __init__(
        self,
        *,
        elevenlabs_client: ElevenLabsVoicesClient,
        initial_selection: VoiceChangerSettings | None,
    ) -> None:
        super().__init__()
        self._elevenlabs_client = elevenlabs_client
        self._initial_selection = initial_selection.model_copy(deep=True) if initial_selection else None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="voice-changer-selector-box"):
            yield Static("[b]Seleccionar voz de ElevenLabs[/b]", id="voice-changer-selector-title")
            yield Static(
                "[dim]Se aplica al audio final del workflow. Elegí una voz o desactivalo con "
                "'Sin voice changer'.[/dim]",
                id="voice-changer-selector-subtitle",
            )
            yield Select[str](
                [(self._render_loading_option(), _LOADING_SENTINEL)],
                value=_LOADING_SENTINEL,
                allow_blank=False,
                id="voice-changer-selector-select",
            )
            yield Static("", id="voice-changer-selector-status")
            with Horizontal(classes="actions-row actions-row-keys"):
                yield Button(
                    "Usar selección",
                    id="voice-changer-selector-confirm",
                    variant="primary",
                    disabled=True,
                )
                yield Button("Cancelar", id="voice-changer-selector-cancel", variant="default")
        yield Footer()

    def on_mount(self) -> None:
        self.app.run_worker(self._load_voices(), exclusive=False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "voice-changer-selector-cancel":
            self.dismiss(None)
            return
        if button_id == "voice-changer-selector-confirm":
            self._handle_confirm()

    def action_cancel(self) -> None:
        self.dismiss(None)

    async def _load_voices(self) -> None:
        try:
            voices = await self._elevenlabs_client.list_voices()
        except Exception as exc:
            self._apply_voice_options([])
            self._set_status(f"{ERROR} no pude listar voces de ElevenLabs: {exc}", error=True)
            return
        visible_count = self._apply_voice_options(voices)
        if visible_count > 0:
            self._set_status(f"{OK} {visible_count} voces cargadas")
        else:
            self._set_status("No hay voces disponibles en ElevenLabs; podés dejarlo sin voice changer.")

    def _apply_voice_options(self, raw_voices: list[dict[str, Any]]) -> int:
        select = self.query_one("#voice-changer-selector-select", Select)
        confirm = self.query_one("#voice-changer-selector-confirm", Button)
        current_voice_id = self._initial_selection.voice_id if self._initial_selection is not None else None

        options: list[tuple[str, str]] = [
            ("Sin voice changer", _NO_VOICE_CHANGER_SENTINEL),
        ]
        seen_voice_ids: set[str] = set()
        if current_voice_id:
            seen_voice_ids.add(current_voice_id)
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
            label = name.strip() if isinstance(name, str) and name.strip() else voice_id
            options.append((f"{label}  ·  {voice_id}", voice_id))
            visible_count += 1
        if current_voice_id and all(value != current_voice_id for _, value in options):
            options.insert(1, (f"Actual (no listada)  ·  {current_voice_id}", current_voice_id))
        select.set_options(options)
        select.value = current_voice_id or _NO_VOICE_CHANGER_SENTINEL
        confirm.disabled = False
        return visible_count

    def _handle_confirm(self) -> None:
        select = self.query_one("#voice-changer-selector-select", Select)
        value = select.value
        if not isinstance(value, str):
            self._set_status(f"{ERROR} elegí una voz válida", error=True)
            return
        if value == _NO_VOICE_CHANGER_SENTINEL:
            self.dismiss(VoiceChangerSelectionResult(voice_changer=None))
            return
        selection = (
            self._initial_selection.model_copy(deep=True)
            if self._initial_selection is not None
            else VoiceChangerSettings(voice_id=value)
        )
        selection.voice_id = value
        self.dismiss(VoiceChangerSelectionResult(voice_changer=selection))

    @staticmethod
    def _render_loading_option() -> str:
        return "Cargando voces…"

    def _set_status(self, message: str, *, error: bool = False) -> None:
        status = self.query_one("#voice-changer-selector-status", Static)
        status.update(f"[red]{message}[/red]" if error else message)
        timeout = _LONG_NOTIFICATION_TIMEOUT if error else _NOTIFICATION_TIMEOUT
        self.notify(
            message,
            severity="error" if error else "information",
            timeout=timeout,
        )


__all__ = [
    "ElevenLabsVoicesClient",
    "VoiceChangerSelectionResult",
    "VoiceChangerSelectorScreen",
]
