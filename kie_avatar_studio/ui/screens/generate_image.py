"""Modal para generar una nueva imagen vía Kie con Nano Banana 2.

Solo dispatch + render (CR-10.1). Valida sintaxis localmente con
`policies` y devuelve `GenerateImageFormResult` vía `dismiss(...)`. La
generación HTTP + persistencia corren en el caller (`ImagesScreen`),
no acá.

El selector de refs (`image_input`) es un `SelectionList` con todos
los assets del catálogo (uploaded + generated) — el usuario puede
elegir hasta 14. Si no elige ninguna, el job corre text-to-image
puro.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Final

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Select, SelectionList, Static, TextArea

from ...domain.errors import ImageGenerationValidationError
from ...domain.models import ImageAssetKind, ImageAssetRef, ImageGenerationSettings
from ...domain.policies import (
    ASPECT_RATIOS,
    MAX_IMAGE_PROMPT_CHARS,
    MAX_IMAGE_REFS,
    OUTPUT_FORMATS,
    RESOLUTIONS,
    validate_image_prompt,
    validate_image_refs,
    validate_image_settings,
)

_FORM_TITLE: Final[str] = "Generar imagen (Nano Banana 2 vía Kie)"


@dataclass(frozen=True, slots=True)
class GenerateImageFormResult:
    """Payload devuelto cuando el usuario confirma el form."""

    label: str
    prompt: str
    settings: ImageGenerationSettings
    refs: list[ImageAssetRef]
    keep_open: bool = False


@dataclass(frozen=True, slots=True)
class GenerateImageFormDefaults:
    """Valores pre-cargados cuando el modal se reabre tras 'Generar y otro'.

    Solo arrastramos settings (aspect, resolution, format); el label y
    el prompt siempre arrancan vacíos para forzar input nuevo.
    """

    settings: ImageGenerationSettings


class GenerateImageFormScreen(ModalScreen[GenerateImageFormResult | None]):
    """Modal con prompt + settings + selector múltiple de refs."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "cancel", "Cancelar", show=False),
    ]

    def __init__(
        self,
        available_refs: list[ImageAssetRef],
        defaults: GenerateImageFormDefaults | None = None,
    ) -> None:
        super().__init__()
        self._available_refs = available_refs
        self._defaults = defaults or GenerateImageFormDefaults(settings=ImageGenerationSettings())
        # Index para resolver refs desde el selection list por su valor.
        self._refs_by_value: dict[str, ImageAssetRef] = {
            _ref_value(ref): ref for ref in available_refs
        }

    def compose(self) -> ComposeResult:
        with Vertical(id="gen-image-dialog"):
            with VerticalScroll(id="gen-image-body"):
                yield Static(_FORM_TITLE, id="gen-image-title")
                yield Label("Nombre de la imagen (label)")
                yield TextArea(id="gen-image-label", language=None)
                yield Label(f"Prompt (máx {MAX_IMAGE_PROMPT_CHARS} chars)")
                yield TextArea(id="gen-image-prompt", language=None)
                yield Static(f"0 / {MAX_IMAGE_PROMPT_CHARS}", id="gen-image-prompt-counter")

                with Horizontal(id="gen-image-settings-row"):
                    with Vertical(classes="gen-image-setting"):
                        yield Label("Aspect ratio")
                        yield Select(
                            options=[(opt, opt) for opt in ASPECT_RATIOS],
                            value=self._defaults.settings.aspect_ratio,
                            allow_blank=False,
                            id="gen-image-aspect",
                        )
                    with Vertical(classes="gen-image-setting"):
                        yield Label("Resolución")
                        yield Select(
                            options=[(opt, opt) for opt in RESOLUTIONS],
                            value=self._defaults.settings.resolution,
                            allow_blank=False,
                            id="gen-image-resolution",
                        )
                    with Vertical(classes="gen-image-setting"):
                        yield Label("Formato")
                        yield Select(
                            options=[(opt, opt) for opt in OUTPUT_FORMATS],
                            value=self._defaults.settings.output_format,
                            allow_blank=False,
                            id="gen-image-format",
                        )

                yield Label(
                    f"Referencias (opcional, máx {MAX_IMAGE_REFS}). "
                    "Espacio para marcar / desmarcar."
                )
                if self._available_refs:
                    yield SelectionList[str](
                        *_ref_options(self._available_refs),
                        id="gen-image-refs",
                    )
                else:
                    yield Static(
                        "[dim]Sin imágenes disponibles para usar como referencia. "
                        "Subí o generá imágenes desde la pantalla Imágenes (I).[/dim]",
                        id="gen-image-refs-empty",
                        classes="form-warning",
                    )

                yield Static("", id="gen-image-error")
            with Horizontal(id="gen-image-footer"):
                yield Button("Cancelar", id="cancel", variant="default")
                yield Button("Generar y otro", id="generate-keep", classes="btn-info")
                yield Button("Generar", id="generate", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#gen-image-label", TextArea).focus()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id == "gen-image-prompt":
            count = len(event.text_area.text)
            counter = self.query_one("#gen-image-prompt-counter", Static)
            over = count > MAX_IMAGE_PROMPT_CHARS
            counter.update(
                f"[red]{count} / {MAX_IMAGE_PROMPT_CHARS} (¡excede!)[/red]"
                if over
                else f"{count} / {MAX_IMAGE_PROMPT_CHARS}"
            )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "cancel":
            self.action_cancel()
            return
        if bid == "generate":
            self._on_generate(keep_open=False)
            return
        if bid == "generate-keep":
            self._on_generate(keep_open=True)
            return

    def action_cancel(self) -> None:
        self.dismiss(None)

    # --- internos ---------------------------------------------------------

    def _on_generate(self, *, keep_open: bool) -> None:
        error = self.query_one("#gen-image-error", Static)
        label = self.query_one("#gen-image-label", TextArea).text.strip()
        if not label:
            error.update("[red]El label no puede estar vacío.[/red]")
            return

        prompt = self.query_one("#gen-image-prompt", TextArea).text.strip()
        try:
            validate_image_prompt(prompt)
        except ImageGenerationValidationError as exc:
            error.update(f"[red]{exc}[/red]")
            return

        settings = ImageGenerationSettings(
            aspect_ratio=_select_string(self.query_one("#gen-image-aspect", Select).value),
            resolution=_select_string(self.query_one("#gen-image-resolution", Select).value),
            output_format=_select_string(self.query_one("#gen-image-format", Select).value),
        )
        try:
            validate_image_settings(settings)
        except ImageGenerationValidationError as exc:
            error.update(f"[red]{exc}[/red]")
            return

        refs = self._selected_refs()
        try:
            validate_image_refs(refs)
        except ImageGenerationValidationError as exc:
            error.update(f"[red]{exc}[/red]")
            return

        self.dismiss(
            GenerateImageFormResult(
                label=label,
                prompt=prompt,
                settings=settings,
                refs=refs,
                keep_open=keep_open,
            )
        )

    def _selected_refs(self) -> list[ImageAssetRef]:
        if not self._available_refs:
            return []
        widget = self.query_one("#gen-image-refs", SelectionList)
        selected_values = list(widget.selected)
        return [
            self._refs_by_value[value] for value in selected_values if value in self._refs_by_value
        ]


def _ref_value(ref: ImageAssetRef) -> str:
    """Valor sintético `kind:id` (mismo patrón que `new_video.py`)."""
    return f"{ref.kind.value}:{ref.id}"


def _ref_label(ref: ImageAssetRef) -> str:
    badge = "[subida]" if ref.kind == ImageAssetKind.UPLOADED else "[generada]"
    return f"{badge} {ref.label}"


def _ref_options(refs: list[ImageAssetRef]) -> list[tuple[str, str]]:
    return [(_ref_label(ref), _ref_value(ref)) for ref in refs]


def _select_string(value: object) -> str:
    """Convierte el `value` del Select (puede ser NoSelection) a string seguro."""
    if isinstance(value, str):
        return value
    # Fallback defensivo (no debería pasar con `allow_blank=False`).
    return ""
