"""Modal `Resumen del workflow` — confirmación final antes de encolar.

Muestra los ajustes resueltos (voice_preset, audio_language,
model_creation method) + el desglose por step de qué operaciones Kie
se van a consumir + el saldo actual de Kie. NO muestra precio
estimado (decisión del rubber-duck): el repo no tiene tabla confiable
de precios y dar montos sin fuente es peor que no darlos.

Flow:

    AutomationScreen → ConfigureWorkflowScreen (edita voice/lang)
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
from ...domain.policies import parse_optional_int_field, resolve_effective_i2v_duration

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
        default_i2v_duration_seconds: int,
    ) -> None:
        super().__init__()
        self._entry = entry
        self._pre_settings = pre_settings
        self._check_credits = check_credits
        # `default_i2v_duration_seconds` viene inyectado por el caller
        # (AutomationScreen lo lee de `Settings`). Sin esto, hardcodear
        # un fallback acá introduce drift con `Settings` cuando el usuario
        # cambia `KIE_DEFAULT_I2V_DURATION_SECONDS` en `.env`.
        self._default_i2v_duration_seconds = default_i2v_duration_seconds

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="workflow-summary-box"):
            yield Static(
                f"[b]Resumen del workflow:[/b] {self._entry.name}",
                id="workflow-summary-title",
            )
            yield Static(
                "[dim]Revisá los ajustes resueltos y el desglose de operaciones que se "
                "van a consumir en Kie. Al confirmar, el workflow se encola y NO se "
                "puede deshacer (los créditos se consumen apenas empiezan los sub-jobs).[/dim]",
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
        voice_preset = (
            self._pre_settings.voice_preset_id or "[dim](sin preset, default voice)[/dim]"
        )
        lines.append(f"  · [b]Voice preset:[/b] {voice_preset}")
        audio_lang = (
            self._pre_settings.audio_language
            or "[dim](sin language_code, modelo multilingual default)[/dim]"
        )
        lines.append(f"  · [b]Audio language:[/b] {audio_lang}")
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
        duration_override = self._pre_settings.i2v_duration_seconds
        if duration_override is not None:
            lines.append(
                f"  · [b]Duración b-roll (FORZADA):[/b] {duration_override}s en TODOS los b-roll"
            )
        else:
            lines.append(
                f"  · [b]Duración b-roll:[/b] "
                f"[dim](la del JSON por step, fallback default {self._default_i2v_duration_seconds}s)[/dim]"
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
        override = self._pre_settings.i2v_duration_seconds
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
            has_text = bool(str(raw_step.get("text", "")).strip())
            include_product_flag = bool(raw_step.get("include_product", False))
            tag = _describe_step_operations(
                type_value, change_scene_flag, has_text, include_product_flag
            )
            # Para b-roll mostramos la duración efectiva (override > step > default)
            duration_tag = ""
            if type_value == StepType.B_ROLL.value:
                # Mismo parser que `infra/workflow_loader._parse_steps` para
                # que la preview NO diverja del runtime ante valores como
                # `"10"` (string numérica) en el JSON.
                step_value = parse_optional_int_field(raw_step.get("duration_seconds"))
                effective = resolve_effective_i2v_duration(
                    override, step_value, self._default_i2v_duration_seconds
                )
                source = _describe_duration_source(override, step_value)
                duration_tag = f"  [dim]{effective}s {source}[/dim]"
            lines.append(
                f"  [b]{step_n}.[/b] [cyan]{type_value:6}[/cyan] {name[:48]}  {tag}{duration_tag}"
            )
        return "\n".join(lines)

    def _render_operations_block(self) -> str:
        counts = _count_operations(self._entry, self._pre_settings)
        lines = ["[b]Operaciones Kie a consumir[/b]"]
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
        if counts["tts"]:
            lines.append(f"  · [green]TTS ElevenLabs:[/green] {counts['tts']}")
        if counts["avatar"]:
            lines.append(f"  · [green]Avatar Pro (a-roll):[/green] {counts['avatar']}")
        if counts["i2v"]:
            lines.append(f"  · [green]Kling 3.0 (b-roll):[/green] {counts['i2v']}")
        total = sum(counts.values())
        lines.append(f"  · [b]Total:[/b] {total} llamadas Kie")
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
    type_value: str, change_scene: bool, has_text: bool, include_product: bool
) -> str:
    """Devuelve un tag corto con las operaciones del step."""
    parts: list[str] = []
    if change_scene or include_product:
        parts.append("scene-img")
    if include_product:
        parts.append("producto")
    if has_text:
        parts.append("tts")
    if type_value == StepType.A_ROLL.value:
        parts.append("avatar")
    else:
        parts.append("i2v")
    return f"[dim]({', '.join(parts)})[/dim]"


def _count_operations(entry: WorkflowEntry, pre_settings: WorkflowPreSettings) -> dict[str, int]:
    """Cuenta operaciones Kie por modelo a partir del JSON del entry."""
    counts = {"nano_banana": 0, "tts": 0, "avatar": 0, "i2v": 0}
    # Imagen base: solo método 'prompt' genera con Nano Banana; los otros
    # métodos no consumen llamadas (local = upload sin Nano Banana,
    # catalog = reusa existente).
    if pre_settings.model_creation.method == ModelCreationMethod.PROMPT:
        counts["nano_banana"] += 1
    steps_payload = (entry.workflow_payload or {}).get("run", [])
    if not isinstance(steps_payload, list):
        return counts
    for raw_step in steps_payload:
        if not isinstance(raw_step, dict):
            continue
        type_value = str(raw_step.get("type", "a-roll"))
        change_scene_flag = bool(
            raw_step.get("change_scene", raw_step.get("change_background", True))
        )
        has_text = bool(str(raw_step.get("text", "")).strip())
        include_product_flag = bool(raw_step.get("include_product", False))
        # Nano Banana se invoca si el step cambia escena O incluye el
        # producto (ambos requieren componer una scene_image nueva).
        if change_scene_flag or include_product_flag:
            counts["nano_banana"] += 1
        if has_text or type_value == StepType.A_ROLL.value:
            counts["tts"] += 1
        if type_value == StepType.A_ROLL.value:
            counts["avatar"] += 1
        else:
            counts["i2v"] += 1
    return counts


def _describe_duration_source(override: int | None, step_value: int | None) -> str:
    """Etiqueta corta que dice de dónde viene la duración mostrada."""
    if override is not None:
        return "(forzado)"
    if step_value is not None:
        return "(del JSON)"
    return "(default)"


__all__ = ["WorkflowSummaryScreen"]
