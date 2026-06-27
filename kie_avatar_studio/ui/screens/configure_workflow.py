from __future__ import annotations

from typing import ClassVar, Final

from loguru import logger
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Button, Select, Static

from ...domain.models import (
    ModelCreationMethod,
    SceneApprovalMode,
    VoiceChangerSettings,
    WorkflowEntry,
    WorkflowPreSettings,
)
from ...domain.policies import I2V_DURATIONS
from ...domain.ports import AudioPreviewPlayer, ElevenLabsVoicesClient
from ._configure_workflow_payload import (
    fallback_model_creation,
    payload_has_b_rolls,
    payload_has_change_scene_b_rolls,
)
from ._configure_workflow_widgets import ConfigureWorkflowView, compose_configure_workflow
from .voice_changer_selector import VoiceChangerSelectionResult, VoiceChangerSelectorScreen

_NOTIFICATION_TIMEOUT: Final[int] = 4
_LONG_NOTIFICATION_TIMEOUT: Final[int] = 6

# Sentinela del Select de duración del B/C-roll: "usar lo que diga cada step
# (o el default global) sin sobreescribir nada". Cualquier otro valor del
# Select es un int de I2V_DURATIONS (3-15) que FORZA esa duración en todos
# los B/C-roll.
_DURATION_AUTO_SENTINEL: Final[str] = "__auto__"


# Resultado del modal:
# (audio_language, i2v_duration_override, scene_approval_mode, voice_changer).
# `None` global = cancelado por el usuario.
ConfigureResult = tuple[
    str | None,
    int | None,
    "SceneApprovalMode | None",
    "VoiceChangerSettings | None",
]


class ConfigureWorkflowScreen(ModalScreen[ConfigureResult | None]):
    """Modal de pre-configuración antes de encolar el workflow."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "cancel", "Cancelar"),
    ]

    def __init__(
        self,
        *,
        entry: WorkflowEntry,
        default_i2v_duration_seconds: int,
        default_scene_approval_mode: SceneApprovalMode,
        elevenlabs_client: ElevenLabsVoicesClient | None = None,
        audio_player: AudioPreviewPlayer | None = None,
    ) -> None:
        super().__init__()
        self._entry = entry
        self._elevenlabs_client = elevenlabs_client
        self._audio_player = audio_player
        pre_payload = (entry.workflow_payload or {}).get("pre_settings", {})
        try:
            self._initial = WorkflowPreSettings.model_validate(pre_payload)
        except Exception:
            self._initial = WorkflowPreSettings(model_creation=fallback_model_creation())
        if isinstance(pre_payload, dict) and "scene_approval_mode" not in pre_payload:
            self._initial.scene_approval_mode = default_scene_approval_mode
        self._voice_changer = (
            self._initial.voice_changer.model_copy(deep=True)
            if self._initial.voice_changer is not None
            else None
        )
        self._has_b_rolls = payload_has_b_rolls(entry.workflow_payload)
        self._has_change_scene_b_rolls = payload_has_change_scene_b_rolls(entry.workflow_payload)
        self._default_i2v_duration_seconds = default_i2v_duration_seconds

    def compose(self) -> ComposeResult:
        yield from compose_configure_workflow(
            ConfigureWorkflowView(
                entry_name=self._entry.name,
                voice_changer_value=self._render_voice_changer_value(),
                voice_changer_hint=self._render_voice_changer_hint(),
                voice_button_disabled=self._elevenlabs_client is None,
                has_b_rolls=self._has_b_rolls,
                duration_options=self._render_duration_options(),
                initial_duration_value=self._initial_duration_value(),
                default_i2v_duration_seconds=self._default_i2v_duration_seconds,
                has_change_scene_b_rolls=self._has_change_scene_b_rolls,
                initial_approval_mode=self._initial.scene_approval_mode,
                promote_product=self._initial.promote_product,
                continue_label=self._render_continue_label(),
                warning_block=self._render_warning_block(),
            )
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "configure-cancel":
            self.dismiss(None)
            return
        if button_id == "configure-confirm":
            self._handle_confirm()
            return
        if button_id == "configure-voice-changer-select":
            self._handle_voice_changer_clicked()

    # --- handlers -----------------------------------------------------

    def _handle_confirm(self) -> None:
        duration_override = self._read_duration_override()
        approval_mode = self._read_approval_mode()
        voice_changer = self._voice_changer.model_copy(deep=True) if self._voice_changer else None
        self.dismiss((None, duration_override, approval_mode, voice_changer))

    def _read_approval_mode(self) -> SceneApprovalMode | None:
        if not self._has_change_scene_b_rolls:
            return None
        try:
            select = self.query_one("#configure-approval-select", Select)
        except Exception:
            return None
        value = select.value
        if not isinstance(value, str):
            return None
        try:
            return SceneApprovalMode(value)
        except ValueError:
            return None

    def _read_duration_override(self) -> int | None:
        if not self._has_b_rolls:
            return None
        try:
            select = self.query_one("#configure-duration-select", Select)
        except Exception:
            return None
        value = select.value
        if not isinstance(value, str) or value == _DURATION_AUTO_SENTINEL:
            return None
        try:
            return int(value)
        except ValueError:
            return None

    def _render_duration_options(self) -> list[tuple[str, str]]:
        options: list[tuple[str, str]] = [
            (
                f"Usar la del JSON / default ({self._default_i2v_duration_seconds}s)",
                _DURATION_AUTO_SENTINEL,
            ),
        ]
        for seconds in I2V_DURATIONS:
            options.append((f"Forzar {seconds}s en todos los B/C-roll", str(seconds)))
        return options

    def _initial_duration_value(self) -> str:
        initial = self._initial.i2v_duration_seconds
        if initial is None:
            return _DURATION_AUTO_SENTINEL
        return str(initial)

    def _handle_voice_changer_clicked(self) -> None:
        if self._elevenlabs_client is None:
            self._set_status(
                "Configura ELEVENLABS_API_KEY en .env para usar el voice changer",
                error=True,
            )
            return
        self.app.push_screen(
            VoiceChangerSelectorScreen(
                elevenlabs_client=self._elevenlabs_client,
                initial_selection=self._voice_changer,
                audio_player=self._audio_player,
            ),
            self._on_voice_changer_dismissed,
        )

    def _on_voice_changer_dismissed(self, result: VoiceChangerSelectionResult | None) -> None:
        if result is None:
            return
        self._voice_changer = (
            result.voice_changer.model_copy(deep=True) if result.voice_changer is not None else None
        )
        self._refresh_voice_changer_summary()

    # --- option renderers ---------------------------------------------

    def _render_voice_changer_value(self) -> str:
        if self._voice_changer is None:
            return "[dim]Sin voice changer[/dim]"
        noise = "sí" if self._voice_changer.remove_background_noise else "no"
        settings = self._render_voice_settings_value()
        settings_suffix = f"  ·  settings={settings}" if settings else ""
        return (
            "[green]Activo[/green]  ·  "
            f"voice_id=[b]{self._voice_changer.voice_id}[/b]  ·  "
            f"modelo={self._voice_changer.model_id}  ·  "
            f"ruido={noise}  ·  "
            f"formato={self._voice_changer.output_format}"
            f"{settings_suffix}"
        )

    def _render_voice_changer_hint(self) -> str:
        if self._elevenlabs_client is None:
            return "[dim]Configura ELEVENLABS_API_KEY en .env para usar el voice changer[/dim]"
        return (
            "[dim]Opcional. Convierte el audio final del workflow con "
            "ElevenLabs speech-to-speech. Configurá voz, modelo STS, "
            "remoción de ruido, formato y voice settings, o dejalo en "
            "'Sin voice changer'.[/dim]"
        )

    def _render_voice_settings_value(self) -> str:
        if self._voice_changer is None or self._voice_changer.voice_settings is None:
            return ""
        payload = self._voice_changer.voice_settings.model_dump(exclude_none=True)
        payload.pop("language_code", None)
        if not payload:
            return ""
        return ", ".join(f"{key}={value}" for key, value in payload.items())

    def _refresh_voice_changer_summary(self) -> None:
        self.query_one("#configure-voice-changer-value", Static).update(
            self._render_voice_changer_value()
        )
        self.query_one("#configure-voice-changer-hint", Static).update(
            self._render_voice_changer_hint()
        )

    def _render_continue_label(self) -> str:
        method = self._initial.model_creation.method
        if method == ModelCreationMethod.PROMPT:
            return "Continuar — generar modelo base"
        if method == ModelCreationMethod.LOCAL:
            return "Continuar — seleccionar imagen"
        return "Continuar al resumen"

    def _render_warning_block(self) -> str:
        method = self._initial.model_creation.method
        if method == ModelCreationMethod.PROMPT:
            return (
                "[b]Próximo paso:[/b] generamos la imagen base con GPT Image 2 y la "
                "previsualizás. Si te gusta, aprobás y pasás al resumen. Si no, podés "
                "regenerarla (gasta otra ronda de créditos) o cancelar."
            )
        if method == ModelCreationMethod.LOCAL:
            return (
                "[b]Próximo paso:[/b] vas a elegir la foto del modelo desde el "
                "filesystem. La subimos a Kie (vive 24h, suficiente para el workflow) "
                "y después verás el resumen final."
            )
        return (
            "[b]Atención:[/b] al continuar verás un resumen final con el desglose de "
            "operaciones Kie que se van a consumir."
        )

    async def action_dismiss(self, result: ConfigureResult | None = None) -> None:
        if result is not None:
            logger.debug(
                "ConfigureWorkflowScreen.action_dismiss invocado con result={!r}, "
                "tratándolo como cancel",
                result,
            )
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

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
