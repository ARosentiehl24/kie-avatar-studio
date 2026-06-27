from __future__ import annotations

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Footer, Header, Select, Static

from ...domain.models import SceneApprovalMode


@dataclass(frozen=True, slots=True)
class ConfigureWorkflowView:
    entry_name: str
    voice_changer_value: str
    voice_changer_hint: str
    voice_button_disabled: bool
    has_b_rolls: bool
    duration_options: list[tuple[str, str]]
    initial_duration_value: str
    default_i2v_duration_seconds: int
    has_change_scene_b_rolls: bool
    initial_approval_mode: SceneApprovalMode
    promote_product: bool
    continue_label: str
    warning_block: str


def compose_configure_workflow(view: ConfigureWorkflowView) -> ComposeResult:
    yield Header(show_clock=False)
    with Vertical(id="configure-workflow-box"):
        yield from _header(view.entry_name)
        yield from _body(
            voice_changer_value=view.voice_changer_value,
            voice_changer_hint=view.voice_changer_hint,
            voice_button_disabled=view.voice_button_disabled,
            has_b_rolls=view.has_b_rolls,
            duration_options=view.duration_options,
            initial_duration_value=view.initial_duration_value,
            default_i2v_duration_seconds=view.default_i2v_duration_seconds,
            has_change_scene_b_rolls=view.has_change_scene_b_rolls,
            initial_approval_mode=view.initial_approval_mode,
            promote_product=view.promote_product,
            warning_block=view.warning_block,
        )
        yield from _actions(view.continue_label)
        yield Static("", id="configure-status-bar")
    yield Footer()


def _header(entry_name: str) -> ComposeResult:
    yield Static(f"[b]Configurar workflow:[/b] {entry_name}", id="configure-workflow-title")
    yield Static(
        "[dim]Estos parámetros se aplican a TODOS los steps del workflow. "
        "Los del JSON aparecen pre-cargados; podés cambiarlos antes de ejecutar.[/dim]",
        id="configure-workflow-subtitle",
    )


def _body(
    *,
    voice_changer_value: str,
    voice_changer_hint: str,
    voice_button_disabled: bool,
    has_b_rolls: bool,
    duration_options: list[tuple[str, str]],
    initial_duration_value: str,
    default_i2v_duration_seconds: int,
    has_change_scene_b_rolls: bool,
    initial_approval_mode: SceneApprovalMode,
    promote_product: bool,
    warning_block: str,
) -> ComposeResult:
    with VerticalScroll(id="configure-workflow-body"):
        yield from _voice_block(voice_changer_value, voice_changer_hint, voice_button_disabled)
        if has_b_rolls:
            yield from _duration_block(
                duration_options,
                initial_duration_value,
                default_i2v_duration_seconds,
            )
        if has_change_scene_b_rolls:
            yield from _approval_block(initial_approval_mode)
        if promote_product:
            yield from _product_block()
        yield Static(warning_block, id="configure-warning")


def _actions(continue_label: str) -> ComposeResult:
    with Horizontal(classes="actions-row actions-row-keys"):
        yield Button(continue_label, id="configure-confirm", variant="primary")
        yield Button("Cancelar", id="configure-cancel", variant="default")


def _product_block() -> ComposeResult:
    yield Static(
        "[b]Producto promocional:[/b] [green]activado[/green] — "
        "al confirmar se te pedirá elegir la imagen del producto "
        "desde tus inputs (se sube a Kie). Los steps con "
        "`include_product=true` lo compondrán sobre la modelo.",
        id="configure-product-info",
    )


def _voice_block(value: str, hint: str, disabled: bool) -> ComposeResult:
    yield Static("[b]Voice changer (ElevenLabs):[/b]")
    with Vertical(id="configure-voice-changer-row"):
        yield Button(
            "Elegir voz y ajustes de ElevenLabs…",
            id="configure-voice-changer-select",
            classes="btn-info",
            disabled=disabled,
        )
        yield Static(value, id="configure-voice-changer-value")
    yield Static(hint, id="configure-voice-changer-hint")


def _duration_block(
    options: list[tuple[str, str]],
    initial_value: str,
    default_seconds: int,
) -> ComposeResult:
    yield Static("[b]Duración del render VEO 3.1 por step:[/b]", id="configure-duration-label")
    with Horizontal(id="configure-duration-row"):
        yield Select[str](
            options=options, value=initial_value, allow_blank=False, id="configure-duration-select"
        )
    yield Static(
        "[dim]Compat legacy: fuerza la duración de TODOS los B/C-roll del "
        "workflow. 'Usar la del JSON / default' deja que cada step use "
        "su `duration_seconds` propio (o el default global de "
        f"{default_seconds}s si no tiene). En workflows v2.0.0 la duración "
        "principal vive en `pre_settings.veo.duration`.[/dim]",
        id="configure-duration-hint",
    )


def _approval_block(initial_mode: SceneApprovalMode) -> ComposeResult:
    yield Static("[b]Revisión manual de escenas B/C-roll:[/b]", id="configure-approval-label")
    with Horizontal(id="configure-approval-row"):
        yield Select[str](
            options=[
                ("auto — generar todo sin revisar escenas", SceneApprovalMode.AUTO.value),
                ("manual — revisar B/C-roll antes de VEO", SceneApprovalMode.MANUAL.value),
            ],
            value=initial_mode.value,
            allow_blank=False,
            id="configure-approval-select",
        )
    yield Static(
        "[dim]En `auto`, A/B/C-roll se ejecutan de punta a punta. En `manual`, "
        "la app pausa solo B/C-roll que generan imagen de escena "
        "(`change_scene=true` o `include_product=true`) para que puedas usarla, "
        "editar prompts y regenerarla, u omitirla antes de gastar VEO 3.1.[/dim]",
        id="configure-approval-hint",
    )
