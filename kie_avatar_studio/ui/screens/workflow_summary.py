"""Modal `Resumen del workflow` — confirmación final antes de encolar.

Muestra los ajustes resueltos (VEO 3.1, voice changer y modelo base),
el desglose por step del pipeline que se va a ejecutar y el saldo
actual de Kie. NO muestra precio estimado (decisión del rubber-duck):
el repo no tiene tabla confiable de precios y dar montos sin fuente es
peor que no darlos.

Flow:

    AutomationScreen → ConfigureWorkflowScreen
        → WorkflowSummaryScreen (review + confirma) → enqueue

Si el usuario cancela en summary, vuelve a la pantalla de automation
sin haber encolado nada.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import ClassVar, Final

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Static

from ...domain.models import (
    ModelCreationMethod,
    SceneApprovalMode,
    StepType,
    WorkflowEntry,
    WorkflowPreSettings,
)

_PROMPT_PREVIEW_MAX_CHARS: Final[int] = 80
_NOTIFICATION_TIMEOUT: Final[int] = 4

CreditsLoader = Callable[[], Awaitable[float | None]]


class WorkflowSummaryScreen(ModalScreen[bool | None]):
    """Resumen final antes de encolar — el usuario confirma o cancela.

    Dismiss con `True` cuando el usuario aprueba (el caller hace el
    enqueue real). Dismiss con `None` cuando cancela. NO ejecuta el
    enqueue acá: es responsabilidad del caller (AutomationScreen) para
    mantener el patrón "modal devuelve resultado, caller actúa".
    """

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "dismiss", "Cancelar"),
    ]

    def __init__(
        self,
        *,
        entry: WorkflowEntry,
        pre_settings: WorkflowPreSettings,
        check_credits: CreditsLoader,
    ) -> None:
        super().__init__()
        self._entry = entry
        self._pre_settings = pre_settings
        self._check_credits = check_credits

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="workflow-summary-box"):
            yield Static(
                f"[b]Resumen del workflow:[/b] {self._entry.name}",
                id="workflow-summary-title",
            )
            yield Static(
                "[dim]Revisá los ajustes resueltos y el desglose de operaciones que se "
                "van a ejecutar en el pipeline VEO 3.1. Al confirmar, el workflow se "
                "encola y NO se puede deshacer (los créditos se consumen apenas empiezan "
                "los renders).[/dim]",
                id="workflow-summary-subtitle",
            )
            with VerticalScroll(id="workflow-summary-body"):
                yield Static(self._render_settings_block(), id="workflow-summary-settings")
                yield Static(self._render_steps_block(), id="workflow-summary-steps")
                yield Static(self._render_operations_block(), id="workflow-summary-ops")
            yield Static("[dim]Consultando saldo de Kie…[/dim]", id="workflow-summary-credits")
            if self._entry.warnings:
                yield Static(
                    self._render_warnings_block(),
                    id="workflow-summary-warning",
                )
            with Horizontal(classes="actions-row actions-row-keys"):
                yield Button("Encolar definitivo", id="summary-confirm", variant="primary")
                yield Button("Volver a editar", id="summary-cancel", variant="default")
            yield Static("", id="workflow-summary-status-bar")
        yield Footer()

    async def on_mount(self) -> None:
        # Consulta el saldo de Kie en background — best-effort, NUNCA bloquea
        # el modal si Kie está down.
        self.run_worker(self._load_credits(), exclusive=True)

    async def _load_credits(self) -> None:
        balance = await self._check_credits()
        try:
            widget = self.query_one("#workflow-summary-credits", Static)
        except Exception:
            return
        if balance is None:
            widget.update(
                "[yellow]Saldo de Kie no disponible (sin key activa o error de red).[/yellow]"
            )
        else:
            widget.update(f"[b]Saldo actual de Kie:[/b] [accent]${balance:.2f} USD[/accent]")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "summary-cancel":
            self.dismiss(None)
            return
        if button_id == "summary-confirm":
            self.dismiss(True)

    # --- block builders (no state) -------------------------------------

    def _render_settings_block(self) -> str:
        creation = self._pre_settings.model_creation
        lines = ["[b]Ajustes resueltos[/b]"]
        veo = self._pre_settings.veo
        lines.append(
            "  · [b]VEO 3.1:[/b] "
            f"model={veo.model} · aspect={veo.aspect_ratio} · res={veo.resolution} "
            f"· duración={veo.duration}s"
        )
        translation = "on" if veo.enable_translation else "off"
        watermark = veo.watermark or "[dim]sin watermark[/dim]"
        lines.append(
            f"    [dim]translation={translation} · watermark={watermark}[/dim]"
        )
        if self._pre_settings.voice_changer is None:
            lines.append("  · [b]Voice changer:[/b] [dim]no configurado[/dim]")
        else:
            changer = self._pre_settings.voice_changer
            noise = "sí" if changer.remove_background_noise else "no"
            lines.append(
                "  · [b]Voice changer:[/b] "
                f"voice_id={changer.voice_id} · model={changer.model_id}"
            )
            lines.append(
                f"    [dim]noise removal={noise} · formato={changer.output_format}[/dim]"
            )
        approval_mode = self._pre_settings.scene_approval_mode
        if approval_mode == SceneApprovalMode.MANUAL:
            lines.append(
                "  · [b]Aprobación scene_image:[/b] "
                "[yellow]MANUAL[/yellow] — el workflow pausará en cada b-roll que "
                "genere scene nueva ([b]change_scene=true[/b] o "
                "[b]include_product=true[/b]) esperando que apruebes la imagen"
            )
        else:
            lines.append("  · [b]Aprobación scene_image:[/b] [dim]auto (sin pausa)[/dim]")
        if (
            self._pre_settings.voice_preset_id is not None
            or self._pre_settings.audio_language is not None
            or self._pre_settings.i2v_duration_seconds is not None
        ):
            lines.append(
                "  · [b]Compat legacy:[/b] "
                "[dim]se detectaron campos del flujo anterior (preset / language / duración).[/dim]"
            )
        lines.append(f"  · [b]Modelo base:[/b] method=[b]{creation.method.value}[/b]")
        if creation.method == ModelCreationMethod.PROMPT and creation.prompt:
            preview = creation.prompt[:_PROMPT_PREVIEW_MAX_CHARS].replace("\n", " ")
            ellipsis = "…" if len(creation.prompt) > _PROMPT_PREVIEW_MAX_CHARS else ""
            lines.append(f"    [dim]prompt: {preview}{ellipsis}[/dim]")
        elif creation.method == ModelCreationMethod.LOCAL and creation.local_path:
            lines.append(f"    [dim]local_path: {creation.local_path}[/dim]")
        elif creation.method == ModelCreationMethod.CATALOG:
            kind = creation.asset_kind.value if creation.asset_kind else "?"
            lines.append(f"    [dim]asset: {kind}/{creation.asset_id or '?'}[/dim]")
        if self._pre_settings.promote_product:
            product = self._pre_settings.product_image
            if product and product.local_path:
                lines.append(f"  · [b]Producto:[/b] {product.local_path}")
            else:
                lines.append("  · [b]Producto:[/b] [dim](se elegirá al confirmar)[/dim]")
        return "\n".join(lines)

    def _render_steps_block(self) -> str:
        steps_payload = (self._entry.workflow_payload or {}).get("run", [])
        if not isinstance(steps_payload, list):
            return ""
        lines = [f"[b]Steps a ejecutar ({len(steps_payload)})[/b]"]
        for raw_step in steps_payload:
            if not isinstance(raw_step, dict):
                continue
            step_n = raw_step.get("step", "?")
            name = str(raw_step.get("scene_name", "?"))
            type_value = str(raw_step.get("type", "?"))
            # Aceptamos ambos nombres (nuevo + legacy) por compat con JSONs viejos.
            change_scene_flag = bool(
                raw_step.get("change_scene", raw_step.get("change_background", True))
            )
            include_product_flag = bool(raw_step.get("include_product", False))
            attached = bool(raw_step.get("attached", True))
            tag = _describe_step_operations(type_value, change_scene_flag, include_product_flag, attached)
            duration_tag = f"  [dim]VEO 3.1 · {self._pre_settings.veo.duration}s[/dim]"
            lines.append(
                f"  [b]{step_n}.[/b] [cyan]{type_value:6}[/cyan] {name[:48]}  {tag}{duration_tag}"
            )
        return "\n".join(lines)

    def _render_operations_block(self) -> str:
        counts = _count_operations(self._entry, self._pre_settings)
        lines = ["[b]Pipeline v2.0.0[/b]"]
        if counts["nano_banana"]:
            base = (
                1 if self._pre_settings.model_creation.method == ModelCreationMethod.PROMPT else 0
            )
            scene = counts["nano_banana"] - base
            detail_parts: list[str] = []
            if base:
                detail_parts.append(f"{base} base")
            if scene:
                detail_parts.append(f"{scene} scene/producto")
            detail = " + ".join(detail_parts)
            lines.append(f"  · [green]Nano Banana 2:[/green] {counts['nano_banana']} ({detail})")
        lines.append(f"  · [green]VEO 3.1:[/green] {counts['veo']} renders ({counts['attached']} adjuntos al final)")
        lines.append("  · [cyan]Postproceso local:[/cyan] concatenación + extracción de audio")
        if counts["voice_changer"]:
            lines.append("  · [cyan]Voice changer:[/cyan] 1 pasada sobre `final_audio.mp3`")
        total = counts["nano_banana"] + counts["veo"]
        lines.append(f"  · [b]Total Kie:[/b] {total} llamadas")
        lines.append(
            "  [dim](Cada llamada consume créditos según el modelo. "
            "No mostramos estimación de monto porque los precios varían — "
            "verificá tu saldo arriba antes de confirmar.)[/dim]"
        )
        return "\n".join(lines)

    def _render_warnings_block(self) -> str:
        lines = ["[b][yellow]Advertencias[/yellow][/b]"]
        for warning in self._entry.warnings:
            lines.append(f"  [yellow]· {warning}[/yellow]")
        return "\n".join(lines)


# --- module-level helpers ---------------------------------------------


def _describe_step_operations(
    type_value: str,
    change_scene: bool,
    include_product: bool,
    attached: bool,
) -> str:
    """Devuelve un tag corto con las operaciones del step."""
    parts: list[str] = []
    if change_scene or include_product:
        parts.append("scene-img")
    if include_product:
        parts.append("producto")
    parts.append("veo")
    parts.append("concat" if attached else "sin-concat")
    if type_value == StepType.A_ROLL.value:
        parts.append("talento")
    else:
        parts.append("recurso")
    return f"[dim]({', '.join(parts)})[/dim]"


def _count_operations(entry: WorkflowEntry, pre_settings: WorkflowPreSettings) -> dict[str, int]:
    """Cuenta operaciones Kie por modelo a partir del JSON del entry."""
    counts = {"nano_banana": 0, "veo": 0, "attached": 0, "voice_changer": 0}
    # Imagen base: solo método 'prompt' genera con Nano Banana; los otros
    # métodos no consumen llamadas (local = upload sin Nano Banana,
    # catalog = reusa existente).
    if pre_settings.model_creation.method == ModelCreationMethod.PROMPT:
        counts["nano_banana"] += 1
    if pre_settings.voice_changer is not None:
        counts["voice_changer"] = 1
    steps_payload = (entry.workflow_payload or {}).get("run", [])
    if not isinstance(steps_payload, list):
        return counts
    for raw_step in steps_payload:
        if not isinstance(raw_step, dict):
            continue
        change_scene_flag = bool(
            raw_step.get("change_scene", raw_step.get("change_background", True))
        )
        include_product_flag = bool(raw_step.get("include_product", False))
        attached = bool(raw_step.get("attached", True))
        # Nano Banana se invoca si el step cambia escena O incluye el
        # producto (ambos requieren componer una scene_image nueva).
        if change_scene_flag or include_product_flag:
            counts["nano_banana"] += 1
        counts["veo"] += 1
        if attached:
            counts["attached"] += 1
    return counts


__all__ = ["WorkflowSummaryScreen"]
