"""Modal `Configurar workflow`: pre-llena preset y voice changer.

El JSON puede traer `voice_preset`, `audio_language` y `voice_changer`
pre-cargados. El modal expone `voice_preset` y un selector opcional de
voz ElevenLabs para `voice_changer`. El `language_code` se configura
DENTRO del preset desde `PresetFormScreen`, así no se duplica el
setting. Si el JSON trae `audio_language`, se respeta tal cual al
ejecutar.

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

### Voice changer

El selector de voice changer se abre en un modal aparte y consulta
`ElevenLabsClient.list_voices()` al abrir. El usuario puede elegir una
voz, o la opción "Sin voice changer" para dejar `voice_changer=None`.

### Dismiss result

El modal se cierra con `dismiss((voice_preset_id, audio_language,
i2v_duration_override, scene_approval_mode, voice_changer))` si el
usuario confirma, o `dismiss(None)` si cancela. El caller
(`AutomationScreen._handle_configure`) registra el callback en el
`push_screen` original. NO usar `await push_screen()` desde el caller:
ese patrón espera al mount, no al dismiss, y causa cascadas de
`InvalidStateError` cuando se intenta dismiss-ear en chain.
"""

from __future__ import annotations

from typing import Any, ClassVar, Final

from loguru import logger
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Select, Static

from ...app_layer.audio_player import AudioPlayer
from ...app_layer.presets_controller import VoicePresetsController
from ...domain.errors import UrlValidationError, VoicePresetValidationError
from ...domain.kie_voice_catalog import get_builtin_voice
from ...domain.models import (
    ModelCreation,
    ModelCreationMethod,
    SceneApprovalMode,
    StepType,
    VoiceChangerSettings,
    VoicePreset,
    WorkflowEntry,
    WorkflowPreSettings,
)
from ...domain.policies import I2V_DURATIONS
from .._icons import ERROR, OK
from .preset_form import PresetFormResult, PresetFormScreen
from .voice_changer_selector import (
    ElevenLabsVoicesClient,
    VoiceChangerSelectionResult,
    VoiceChangerSelectorScreen,
)

_NOTIFICATION_TIMEOUT: Final[int] = 4
_LONG_NOTIFICATION_TIMEOUT: Final[int] = 6

# Valor especial del Select que representa "sin preset, usar default voice".
_NO_PRESET_SENTINEL: Final[str] = "__no_preset__"

# Sentinela del Select de duración del b-roll: "usar lo que diga cada step
# (o el default global) sin sobreescribir nada". Cualquier otro valor del
# Select es un int de I2V_DURATIONS (3-15) que FORZA esa duración en todos
# los b-roll.
_DURATION_AUTO_SENTINEL: Final[str] = "__auto__"


# Resultado del modal:
# (voice_preset_id, audio_language, i2v_duration_override,
#  scene_approval_mode, voice_changer).
# `None` global = cancelado por el usuario.
ConfigureResult = tuple[
    str | None,
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
        presets_controller: VoicePresetsController,
        audio_player: AudioPlayer,
        default_i2v_duration_seconds: int,
        elevenlabs_client: ElevenLabsVoicesClient | None = None,
    ) -> None:
        super().__init__()
        self._entry = entry
        self._presets_controller = presets_controller
        self._audio_player = audio_player
        self._elevenlabs_client = elevenlabs_client
        self._presets: list[VoicePreset] = []
        pre_payload = (entry.workflow_payload or {}).get("pre_settings", {})
        try:
            self._initial = WorkflowPreSettings.model_validate(pre_payload)
        except Exception:
            self._initial = WorkflowPreSettings(model_creation=_fallback_model_creation())
        self._voice_changer = (
            self._initial.voice_changer.model_copy(deep=True)
            if self._initial.voice_changer is not None
            else None
        )
        # El selector de duración del b-roll solo aparece si el workflow
        # tiene al menos un step b-roll. Workflows 100% a-roll no tienen
        # nada que configurar (la duración del avatar la define el audio).
        self._has_b_rolls = _payload_has_b_rolls(entry.workflow_payload)
        # El selector de scene_approval_mode solo aparece si hay al menos
        # un b-roll que genera scene nueva (`change_scene=true` o
        # `include_product=true`, ver `needs_scene_generation`): ese es el
        # único caso donde la pausa de aprobación tiene sentido — sin scene
        # nueva no hay imagen que aprobar.
        self._has_change_scene_b_rolls = _payload_has_change_scene_b_rolls(entry.workflow_payload)
        # Para mostrar la duración del default ACTUAL (de `Settings`) en
        # la copy del Select y del hint sin que se desincronice con .env
        # cuando el operador cambia `KIE_DEFAULT_I2V_DURATION_SECONDS`.
        self._default_i2v_duration_seconds = default_i2v_duration_seconds

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
            with VerticalScroll(id="configure-workflow-body"):
                yield Static("[b]Voice preset (registrado en pantalla Presets):[/b]")
                with Horizontal(id="configure-preset-row"):
                    yield Select[str](
                        [(self._render_loading_option(), _NO_PRESET_SENTINEL)],
                        allow_blank=False,
                        id="configure-voice-preset-select",
                    )
                    yield Button(
                        "▶ Preview",
                        id="configure-preview-preset",
                        classes="btn-info",
                    )
                    yield Button(
                        "■ Detener",
                        id="configure-preview-stop",
                        classes="btn-warning",
                    )
                    yield Button(
                        "Crear nuevo preset",
                        id="configure-create-preset",
                        classes="btn-info",
                    )
                yield Static(
                    "[dim]Si no encontrás el preset que buscás, presioná "
                    "'Crear nuevo preset' para registrarlo sin salir de este modal. "
                    "Si el workflow usa compat legacy, el `language_code` se resuelve "
                    "dentro del preset; los workflows v2.0.0 usan `voice_changer` "
                    "directamente desde el JSON.[/dim]",
                    id="configure-preset-hint",
                )
                yield Static("[b]Voice changer (ElevenLabs):[/b]")
                with Horizontal(id="configure-voice-changer-row"):
                    yield Static(
                        self._render_voice_changer_value(),
                        id="configure-voice-changer-value",
                    )
                    yield Button(
                        "Seleccionar voz…",
                        id="configure-voice-changer-select",
                        classes="btn-info",
                        disabled=self._elevenlabs_client is None,
                    )
                yield Static(
                    self._render_voice_changer_hint(),
                    id="configure-voice-changer-hint",
                )
                if self._has_b_rolls:
                    yield Static(
                        "[b]Duración del render VEO 3.1 por step:[/b]",
                        id="configure-duration-label",
                    )
                    with Horizontal(id="configure-duration-row"):
                        yield Select[str](
                            options=self._render_duration_options(),
                            value=self._initial_duration_value(),
                            allow_blank=False,
                            id="configure-duration-select",
                        )
                    yield Static(
                        "[dim]Compat legacy: fuerza la duración de TODOS los b-roll del "
                        "workflow. 'Usar la del JSON / default' deja que cada step use "
                        "su `duration_seconds` propio (o el default global de "
                        f"{self._default_i2v_duration_seconds}s si no tiene). "
                        "En workflows v2.0.0 la duración principal vive en "
                        "`pre_settings.veo.duration`.[/dim]",
                        id="configure-duration-hint",
                    )
                if self._has_change_scene_b_rolls:
                    yield Static(
                        "[b]Aprobación de scene_image:[/b]",
                        id="configure-approval-label",
                    )
                    with Horizontal(id="configure-approval-row"):
                        yield Select[str](
                            options=[
                                (
                                    "auto — sigue automáticamente al render",
                                    SceneApprovalMode.AUTO.value,
                                ),
                                (
                                    "manual — pausar y aprobar cada scene_image",
                                    SceneApprovalMode.MANUAL.value,
                                ),
                            ],
                            value=self._initial.scene_approval_mode.value,
                            allow_blank=False,
                            id="configure-approval-select",
                        )
                    yield Static(
                        "[dim]Solo aplica a b-roll que genera scene nueva "
                        "(`change_scene=true` o `include_product=true`). "
                        "Modo `manual` pausa el workflow después de generar la "
                        "scene_image con Nano Banana y espera que apruebes / "
                        "regeneres / canceles desde la pantalla Automatización. "
                        "Evita gastar créditos en VEO 3.1 animando una scene "
                        "que salió mal.[/dim]",
                        id="configure-approval-hint",
                    )
                if self._initial.promote_product:
                    yield Static(
                        "[b]Producto promocional:[/b] [green]activado[/green] — "
                        "al confirmar se te pedirá elegir la imagen del producto "
                        "desde tus inputs (se sube a Kie). Los steps con "
                        "`include_product=true` lo compondrán sobre la modelo.",
                        id="configure-product-info",
                    )
                yield Static(
                    self._render_warning_block(),
                    id="configure-warning",
                )
            with Horizontal(classes="actions-row actions-row-keys"):
                yield Button(
                    self._render_continue_label(),
                    id="configure-confirm",
                    variant="primary",
                )
                yield Button("Cancelar", id="configure-cancel", variant="default")
            yield Static("", id="configure-status-bar")
        yield Footer()

    async def on_mount(self) -> None:
        await self._refresh_presets_select()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "configure-cancel":
            self._stop_preview_async()
            self.dismiss(None)
            return
        if button_id == "configure-confirm":
            self._stop_preview_async()
            self._handle_confirm()
            return
        if button_id == "configure-create-preset":
            self._handle_create_preset_clicked()
            return
        if button_id == "configure-preview-preset":
            self._handle_preview_preset()
            return
        if button_id == "configure-preview-stop":
            self._stop_preview_async()
            return
        if button_id == "configure-voice-changer-select":
            self._handle_voice_changer_clicked()

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

    def _handle_confirm(self) -> None:
        """Cierra el modal con el resultado para que el caller decida el siguiente paso.

        NO ejecuta el siguiente modal acá (sería un anti-patrón en Textual:
        `await push_screen()` espera al mount, no al dismiss, y dispara
        `InvalidStateError` cuando este modal intenta cerrarse después).
        El caller registra un callback en su `push_screen(this, callback)`
        y dispatcha desde ahí.
        """
        preset_value = self._current_select_value()
        voice_preset_id = (
            None if preset_value is None or preset_value == _NO_PRESET_SENTINEL else preset_value
        )
        # `audio_language` ya NO se pide en este modal: el `language_code`
        # del preset (configurable en `PresetFormScreen`) tiene prioridad.
        # Si el JSON trae `audio_language`, se respeta tal cual (backdoor).
        duration_override = self._read_duration_override()
        approval_mode = self._read_approval_mode()
        voice_changer = self._voice_changer.model_copy(deep=True) if self._voice_changer else None
        self.dismiss((voice_preset_id, None, duration_override, approval_mode, voice_changer))

    def _read_approval_mode(self) -> SceneApprovalMode | None:
        """Lee el Select de scene_approval_mode. `None` = no override (usa el del JSON)."""
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
        """Lee el Select de duración del b-roll. `None` = sin override."""
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
        """Opciones del Select: 'Usar JSON/default' + cada I2V_DURATIONS."""
        options: list[tuple[str, str]] = [
            (
                f"Usar la del JSON / default ({self._default_i2v_duration_seconds}s)",
                _DURATION_AUTO_SENTINEL,
            ),
        ]
        for seconds in I2V_DURATIONS:
            options.append((f"Forzar {seconds}s en todos los b-roll", str(seconds)))
        return options

    def _initial_duration_value(self) -> str:
        """Si el JSON ya trae `pre_settings.i2v_duration_seconds`, lo respeta."""
        initial = self._initial.i2v_duration_seconds
        if initial is None:
            return _DURATION_AUTO_SENTINEL
        return str(initial)

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

    def _render_voice_changer_value(self) -> str:
        if self._voice_changer is None:
            return "[dim]Sin voice changer[/dim]"
        return (
            "[green]Activo[/green]  ·  "
            f"voice_id=[b]{self._voice_changer.voice_id}[/b]  ·  "
            f"modelo={self._voice_changer.model_id}"
        )

    def _render_voice_changer_hint(self) -> str:
        if self._elevenlabs_client is None:
            return (
                "[dim]Configura ELEVENLABS_API_KEY en .env para usar el voice changer[/dim]"
            )
        return (
            "[dim]Opcional. Convierte el audio final del workflow con "
            "ElevenLabs speech-to-speech. Elegí una voz o dejalo en "
            "'Sin voice changer'.[/dim]"
        )

    def _refresh_voice_changer_summary(self) -> None:
        self.query_one("#configure-voice-changer-value", Static).update(
            self._render_voice_changer_value()
        )
        self.query_one("#configure-voice-changer-hint", Static).update(
            self._render_voice_changer_hint()
        )

    def _render_continue_label(self) -> str:
        """El botón de continuar describe el próximo paso según el método."""
        method = self._initial.model_creation.method
        if method == ModelCreationMethod.PROMPT:
            return "Continuar — generar modelo base"
        if method == ModelCreationMethod.LOCAL:
            return "Continuar — seleccionar imagen"
        return "Continuar al resumen"

    def _render_warning_block(self) -> str:
        """Mensaje de qué pasa cuando el usuario aprieta 'Continuar'."""
        method = self._initial.model_creation.method
        if method == ModelCreationMethod.PROMPT:
            return (
                "[b]Próximo paso:[/b] generamos la imagen base con Nano Banana 2 y la "
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

    # --- preview de voz del preset seleccionado --------------------------

    def _handle_preview_preset(self) -> None:
        """Reproduce el preview de la voz del preset seleccionado.

        Lookup: id del Select → VoicePreset → voice_id → builtin voice
        del catálogo Kie → preview_url. El AudioPlayer se encarga de
        auto-cancelar cualquier preview en curso.
        """
        preset_value = self._current_select_value()
        if preset_value is None or preset_value == _NO_PRESET_SENTINEL:
            self._set_status(
                f"{ERROR} elegí un preset primero para escuchar su voz",
                error=True,
            )
            return
        preset = self._find_preset_by_id(preset_value)
        if preset is None:
            self._set_status(f"{ERROR} preset no encontrado en el store", error=True)
            return
        voice = get_builtin_voice(preset.voice_id)
        if voice is None:
            self._set_status(
                f"{ERROR} voice_id {preset.voice_id!r} no está en el catálogo built-in",
                error=True,
            )
            return
        if not voice.preview_url:
            self._set_status(
                f"{ERROR} la voz '{voice.label}' no tiene preview disponible",
                error=True,
            )
            return
        self.app.run_worker(self._play_preview(voice.preview_url), exclusive=False)

    async def _play_preview(self, url: str) -> None:
        try:
            await self._audio_player.play_voice_preview(url)
        except (OSError, UrlValidationError) as exc:
            self._set_status(f"{ERROR} no pude reproducir el preview: {exc}", error=True)

    def _stop_preview_async(self) -> None:
        """Detiene cualquier preview en curso de forma idempotente."""
        self.app.run_worker(self._audio_player.stop(), exclusive=False)

    def _find_preset_by_id(self, preset_id: str) -> VoicePreset | None:
        for preset in self._presets:
            if preset.id == preset_id:
                return preset
        return None

    async def action_dismiss(self, result: ConfigureResult | None = None) -> None:
        # Override defensivo de Screen.action_dismiss. Nuestro binding es
        # Esc→cancel, pero Textual puede dispararlo internamente desde
        # otros paths (ej. close-via-modal-stack). Aseguramos stop del
        # preview + downgrade silencioso del result a None (cualquier
        # confirmación llega por el botón Confirmar, no por este path).
        if result is not None:
            logger.debug(
                "ConfigureWorkflowScreen.action_dismiss invocado con result={!r}, "
                "tratándolo como cancel",
                result,
            )
        self._stop_preview_async()
        self.dismiss(None)

    def action_cancel(self) -> None:
        """Atajo Esc + handler del binding: stop del preview + cancelar."""
        self._stop_preview_async()
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


def _fallback_model_creation() -> ModelCreation:
    """Fallback cuando el JSON no trae pre_settings completos."""
    return ModelCreation(method=ModelCreationMethod.PROMPT, prompt="A photorealistic person")


def _payload_has_b_rolls(payload: dict[str, Any] | None) -> bool:
    """Detecta si el workflow tiene al menos un step de tipo b-roll.

    Si el payload está malformado o vacío, conservadoramente asume que
    SÍ hay b-rolls para no esconder el control de duración. Es mejor
    mostrar un Select irrelevante que ocultar uno crítico.
    """
    if not isinstance(payload, dict):
        return True
    steps = payload.get("run", [])
    if not isinstance(steps, list):
        return True
    for step in steps:
        if not isinstance(step, dict):
            continue
        step_type = step.get("type", "")
        if step_type == StepType.B_ROLL.value:
            return True
    return False


def _payload_has_change_scene_b_rolls(payload: dict[str, Any] | None) -> bool:
    """`True` si hay al menos un b-roll que genera scene nueva con Nano Banana.

    Genera scene nueva = `change_scene=true` (o legacy `change_background`)
    **o** `include_product=true`. Solo en ese caso tiene sentido mostrar el
    Select de `scene_approval_mode`: si todos los b-roll reusan la imagen
    base tal cual, no hay scene_image que aprobar y el modal no necesita el
    control.
    """
    if not isinstance(payload, dict):
        return True
    steps = payload.get("run", [])
    if not isinstance(steps, list):
        return True
    for step in steps:
        if not isinstance(step, dict):
            continue
        if step.get("type", "") != StepType.B_ROLL.value:
            continue
        # Acepta nombre nuevo + alias legacy. Default True.
        change = step.get("change_scene", step.get("change_background", True))
        if bool(change) or bool(step.get("include_product", False)):
            return True
    return False


__all__ = ["ConfigureResult", "ConfigureWorkflowScreen"]
