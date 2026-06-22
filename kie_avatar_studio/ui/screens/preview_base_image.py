"""Modal para previsualizar la imagen base ANTES de encolar un workflow.

Cuando el workflow especifica ``model_creation.method='prompt'``, la imagen
base de la modelo se genera con GPT Image 2 antes de cualquier step. Como
esto consume créditos y la imagen base condiciona TODO el resto del flujo
(a-roll = lip-sync sobre esta cara, b-roll = misma cara en otros entornos),
le damos al usuario la chance de:

1. Revisar el prompt completo (puede editarlo aquí mismo si quiere ajustar).
2. Configurar `aspect_ratio`, `resolution`, `output_format` para esta generación.
3. Disparar la generación ON DEMAND (no se autogenera al abrir).
4. Aprobar el resultado o regenerar con cambios.
5. Cancelar sin gastar créditos extra.

El modal devuelve `ImageAssetRef` aprobado o `None` si el usuario cancela.
"""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import ClassVar

from loguru import logger
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, LoadingIndicator, Select, Static, TextArea

from ...app_layer.workflow_controller import WorkflowController
from ...domain.models import ImageAssetRef, ImageGenerationSettings
from ...domain.policies import ASPECT_RATIOS, OUTPUT_FORMATS, RESOLUTIONS

_DEFAULT_BASE_PROMPT_MODEL = "gpt-image-2-text-to-image"


def _model_label_for_status(model: str) -> str:
    """Devuelve una etiqueta humana para el modelo de generación base."""
    normalized = model.strip().lower()
    if normalized == "gpt-image-2-text-to-image":
        return "GPT Image 2"
    if normalized == "nano-banana-2":
        return "Nano Banana 2"
    return model


class PreviewBaseImageScreen(ModalScreen[ImageAssetRef | None]):
    """Permite previsualizar la imagen base generada antes de encolar el workflow."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "cancel", "Cancelar"),
    ]

    def __init__(
        self,
        *,
        controller: WorkflowController,
        prompt: str,
        label_hint: str,
        initial_settings: ImageGenerationSettings | None = None,
        open_local_path: Callable[[Path], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__()
        self._controller = controller
        self._initial_prompt = prompt
        self._label_hint = label_hint
        self._initial_settings = initial_settings or ImageGenerationSettings()
        self._open_local_path = open_local_path
        self._current_ref: ImageAssetRef | None = None
        self._current_local_path: Path | None = None
        # Acumulamos paths de TODAS las regeneraciones para limpiar las
        # descartadas cuando el usuario aprueba/cierra el modal.
        self._all_preview_paths: list[Path] = []
        # Si el usuario cierra mid-flight, evitamos race con el worker que
        # podría intentar descargar después del dismiss y dejar archivos
        # huérfanos.
        self._cancelled = False
        self._generating = False

    def compose(self) -> ComposeResult:
        with Vertical(id="preview-base-box"):
            yield Label("[b]Previsualizar imagen base[/b]", id="preview-base-title")
            yield Static(
                f"Modelo del workflow [b]{self._label_hint}[/b]. Ajustá los settings "
                "y el prompt si querés y dispará la generación. Podés regenerar "
                "tantas veces como necesites antes de aprobar.",
                id="preview-base-subtitle",
            )
            yield Label("[b]Prompt[/b] (editable)", classes="preview-base-section")
            yield TextArea(self._initial_prompt, id="preview-base-prompt-input")
            yield Label("[b]Settings[/b]", classes="preview-base-section")
            with Horizontal(id="preview-base-settings-row"):
                yield Select(
                    [(v, v) for v in ASPECT_RATIOS],
                    value=self._initial_settings.aspect_ratio,
                    prompt="Aspect ratio",
                    id="preview-base-aspect",
                )
                yield Select(
                    [(v, v) for v in RESOLUTIONS],
                    value=self._initial_settings.resolution,
                    prompt="Resolution",
                    id="preview-base-resolution",
                )
                yield Select(
                    [(v, v) for v in OUTPUT_FORMATS],
                    value=self._initial_settings.output_format,
                    prompt="Formato",
                    id="preview-base-format",
                )
            with VerticalScroll(id="preview-base-result-scroll"):
                # Indicador de carga adentro del scroll, junto al mensaje:
                # cuando está `display=True` aparece arriba del status text
                # con un spinner ASCII animado a ~10fps. Sin esto la
                # generación parece colgada (la HTTP request de generación
                # tarda 15-30s).
                yield LoadingIndicator(id="preview-base-loader")
                yield Static(
                    "[dim]Aún no has generado la imagen base. Ajustá settings "
                    "y prompt y presioná [b]Generar imagen[/b].[/]",
                    id="preview-base-result",
                )
            with Horizontal(id="preview-base-actions"):
                yield Button("Generar imagen", id="preview-base-generate", variant="primary")
                yield Button(
                    "Aprobar y continuar",
                    id="preview-base-approve",
                    classes="btn-success",
                    disabled=True,
                )
                yield Button(
                    "Abrir en visor",
                    id="preview-base-open",
                    classes="btn-info",
                    disabled=True,
                )
                yield Button("Cancelar", id="preview-base-cancel", variant="default")

    def _read_settings(self) -> ImageGenerationSettings:
        aspect = self.query_one("#preview-base-aspect", Select).value
        resolution = self.query_one("#preview-base-resolution", Select).value
        output_format = self.query_one("#preview-base-format", Select).value
        return ImageGenerationSettings(
            model=self._initial_settings.model,
            aspect_ratio=str(aspect)
            if aspect is not Select.BLANK
            else self._initial_settings.aspect_ratio,
            resolution=str(resolution)
            if resolution is not Select.BLANK
            else self._initial_settings.resolution,
            output_format=str(output_format)
            if output_format is not Select.BLANK
            else self._initial_settings.output_format,
        )

    def _read_prompt(self) -> str:
        return self.query_one("#preview-base-prompt-input", TextArea).text.strip()

    def _set_busy(self, busy: bool, message: str) -> None:
        """Bloquea controles + muestra el spinner mientras se genera.

        Durante la generación: spinner visible, botón Generar deshabilitado
        con label dinámico, Selects + TextArea deshabilitados (evita que
        el usuario cambie params mid-flight pensando que abortar es libre).
        Cancelar queda habilitado siempre para abortar.
        """
        self._generating = busy
        generate_btn = self.query_one("#preview-base-generate", Button)
        generate_btn.disabled = busy
        generate_btn.label = "Generando…" if busy else "Generar imagen"
        # Inputs del form deshabilitados durante la generación: si el
        # usuario cambia algo mid-flight piensa que se va a aplicar al
        # render actual y no es así.
        for widget_id in (
            "preview-base-prompt-input",
            "preview-base-aspect",
            "preview-base-resolution",
            "preview-base-format",
        ):
            self.query_one(f"#{widget_id}").disabled = busy
        # Aprobar / abrir solo si ya hay imagen Y no estamos generando.
        approve = self.query_one("#preview-base-approve", Button)
        open_btn = self.query_one("#preview-base-open", Button)
        if busy or self._current_local_path is None:
            approve.disabled = True
            open_btn.disabled = True
        else:
            approve.disabled = False
            open_btn.disabled = False
        # Spinner: visible solo cuando estamos generando.
        self.query_one("#preview-base-loader", LoadingIndicator).display = busy
        self.query_one("#preview-base-result", Static).update(message)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Despacha cada acción a un worker para no bloquear el message pump.

        Patrón canónico del repo (ver `audios.py`, `images.py`, `automation.py`):
        el handler retorna inmediato, el worker corre independiente. Esto:
        - Permite que Textual repinte el spinner ANTES de la HTTP larga.
        - Mantiene Cancelar responsive durante toda la generación (sin
          bloquear el pump del Screen con el `await` de Generate).
        - Habilita doble-click guard puro vía `Button.disabled`
          (set sincrónicamente en `_set_busy` antes del worker).
        """
        bid = event.button.id
        if bid == "preview-base-generate":
            self.app.run_worker(self._generate_once(), exclusive=False)
            return
        if bid == "preview-base-approve":
            self.app.run_worker(self._approve(), exclusive=False)
            return
        if bid == "preview-base-open":
            self.app.run_worker(self._open_in_viewer(), exclusive=False)
            return
        if bid == "preview-base-cancel":
            self.action_cancel()

    async def _generate_once(self) -> None:
        if self._generating:
            return
        prompt = self._read_prompt()
        if not prompt:
            self.query_one("#preview-base-result", Static).update(
                "[red]El prompt no puede estar vacío.[/]"
            )
            return
        settings = self._read_settings()
        model_label = _model_label_for_status(settings.model or _DEFAULT_BASE_PROMPT_MODEL)
        self._set_busy(True, f"[yellow]Generando imagen base con {model_label}…[/]")
        try:
            ref, local_path = await self._controller.preview_base_from_prompt(
                prompt,
                label_hint=self._label_hint,
                settings=settings,
            )
        except Exception as exc:
            logger.exception("workflow.preview_base.generate failed")
            if self._cancelled:
                return
            self._set_busy(False, f"[red]Falló la generación: {exc}[/]")
            return
        if self._cancelled:
            with contextlib.suppress(OSError):
                local_path.unlink(missing_ok=True)
            return
        self._current_ref = ref
        self._current_local_path = local_path
        self._all_preview_paths.append(local_path)
        self._set_busy(
            False,
            f"[green]✓ Generada en[/] [b]{local_path}[/b]\n\n"
            "Revisala. Si te gusta presioná [b]Aprobar[/b]. Si no, ajustá "
            "settings/prompt y presioná [b]Generar imagen[/b] de nuevo.",
        )

    async def _approve(self) -> None:
        if self._current_ref is None or self._current_local_path is None:
            return
        self._cleanup_discarded_previews(keep=self._current_local_path)
        self.dismiss(self._current_ref)

    async def _open_in_viewer(self) -> None:
        if self._current_local_path is None or self._open_local_path is None:
            return
        try:
            await self._open_local_path(self._current_local_path)
        except Exception as exc:
            logger.exception("workflow.preview_base.open_viewer failed")
            # Feedback visible al usuario: el log silencioso deja al
            # usuario sin saber por qué nada se abrió.
            with contextlib.suppress(Exception):
                self.query_one("#preview-base-result", Static).update(
                    f"[red]No pude abrir el visor del sistema: {exc}[/red]"
                )

    def action_cancel(self) -> None:
        """Cancela el modal: limpia previews descartados y cierra con `None`.

        Usado tanto por el botón Cancelar como por el binding Esc.
        Marca `_cancelled=True` para que el worker en flight no toque
        widgets desmontados ni deje archivos huérfanos.
        """
        self._cancelled = True
        self._cleanup_discarded_previews(keep=None)
        self.dismiss(None)

    async def action_dismiss(self, result: ImageAssetRef | None = None) -> None:
        # Override defensivo: si Textual dispara action_dismiss internamente
        # (no via nuestro binding Esc→cancel), igual queremos limpiar los
        # previews acumulados para no dejar archivos huérfanos en
        # `outputs/_previews/`. El path de aprobar pasa por `_approve` que
        # ya hace su propio cleanup con keep=path.
        if result is None:
            self._cancelled = True
            self._cleanup_discarded_previews(keep=None)
        self.dismiss(result)

    def _cleanup_discarded_previews(self, *, keep: Path | None) -> None:
        for path in self._all_preview_paths:
            if keep is not None and path == keep:
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("workflow.preview_base.cleanup_failed path={}", path)
