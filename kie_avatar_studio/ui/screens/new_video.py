"""Modal para crear un `VideoJob` desde assets ya en Kie (Modo B).

Selects de imagen + audio (poblados desde los stores locales) +
TextArea para el prompt + botones Cancelar / Generar. Solo dispatch
+ render (CR-10.1): la validación de assets expirados y la
construcción del `VideoJob` viven en `VideosController`.

El usuario puede previsualizar el audio seleccionado antes de
generar el video, reusando el mismo `AudioPlayer` que la pantalla
Audios.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Final

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static, TextArea

from ...app_layer.audio_player import AudioPlayer
from ...domain.errors import UrlValidationError
from ...domain.models import GeneratedAudio, UploadedImage

_FORM_TITLE: Final[str] = "Generar video desde imagen + audio existentes"
_PROMPT_MAX_CHARS: Final[int] = 5000
_NO_ASSETS_MSG: Final[str] = (
    "No hay {kind} cargados todavía. Primero usá la pantalla '{screen}' para crear/cargar."
)


@dataclass(frozen=True, slots=True)
class NewVideoFormResult:
    """Payload devuelto cuando el usuario confirma el form."""

    image_id: str
    audio_id: str
    prompt: str


class NewVideoFormScreen(ModalScreen[NewVideoFormResult | None]):
    """Modal con Select imagen + Select audio + TextArea prompt."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "cancel", "Cancelar", show=False),
    ]

    def __init__(
        self,
        images: list[UploadedImage],
        audios: list[GeneratedAudio],
        audio_player: AudioPlayer,
    ) -> None:
        super().__init__()
        self._images = images
        self._audios = audios
        self._audio_player = audio_player

    def compose(self) -> ComposeResult:
        # Mismo patrón que el modal Generar Audio: outer Vertical (no
        # scroll) + body scrollable + footer sticky. Evita que el
        # scrollbar atraviese los botones de acción.
        with Vertical(id="video-form-dialog"):
            with VerticalScroll(id="video-form-body"):
                yield Static(_FORM_TITLE, id="video-form-title")
                yield Label("Imagen (de la pantalla Imágenes)")
                yield Select(
                    options=_image_options(self._images),
                    allow_blank=not self._images,
                    id="video-image",
                )
                if not self._images:
                    yield Static(
                        _NO_ASSETS_MSG.format(kind="imágenes", screen="Imágenes"),
                        id="video-image-warning",
                        classes="form-warning",
                    )

                yield Label("Audio TTS (de la pantalla Audios)")
                with Horizontal(id="video-audio-row"):
                    yield Select(
                        options=_audio_options(self._audios),
                        allow_blank=not self._audios,
                        id="video-audio",
                    )
                    yield Button("🔊 Preview", id="video-audio-preview", classes="btn-info")
                    yield Button("⏹", id="video-audio-stop", classes="btn-glyph")
                if not self._audios:
                    yield Static(
                        _NO_ASSETS_MSG.format(kind="audios", screen="Audios"),
                        id="video-audio-warning",
                        classes="form-warning",
                    )

                yield Label(
                    f"Prompt para el avatar (máx {_PROMPT_MAX_CHARS} chars). "
                    "Describí escena, cámara, ambiente."
                )
                yield TextArea(id="video-prompt", language=None)
                yield Static(f"0 / {_PROMPT_MAX_CHARS}", id="video-prompt-counter")

                yield Static("", id="video-form-error")
            with Horizontal(id="video-form-footer"):
                yield Button("Cancelar", id="cancel", variant="default")
                yield Button("Generar video", id="generate", variant="primary")

    def on_mount(self) -> None:
        # Si hay assets, dejamos el primero seleccionado por defecto.
        if self._images:
            select = self.query_one("#video-image", Select)
            select.value = self._images[0].id
        if self._audios:
            select = self.query_one("#video-audio", Select)
            select.value = self._audios[0].id
        self.query_one("#video-prompt", TextArea).focus()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id == "video-prompt":
            count = len(event.text_area.text)
            counter = self.query_one("#video-prompt-counter", Static)
            over = count > _PROMPT_MAX_CHARS
            counter.update(
                f"[red]{count} / {_PROMPT_MAX_CHARS} (¡excede!)[/red]"
                if over
                else f"{count} / {_PROMPT_MAX_CHARS}"
            )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "cancel":
            self.action_cancel()
            return
        if bid == "generate":
            await self._on_generate()
            return
        if bid == "video-audio-preview":
            await self._on_preview()
            return
        if bid == "video-audio-stop":
            await self._audio_player.stop()
            return

    def action_cancel(self) -> None:
        self.dismiss(None)

    # --- internos ---------------------------------------------------------

    async def _on_generate(self) -> None:
        image_select = self.query_one("#video-image", Select)
        audio_select = self.query_one("#video-audio", Select)
        prompt_area = self.query_one("#video-prompt", TextArea)
        error = self.query_one("#video-form-error", Static)

        image_id = image_select.value
        audio_id = audio_select.value
        prompt = prompt_area.text.strip()

        if not isinstance(image_id, str) or not image_id:
            error.update("[red]Elegí una imagen.[/red]")
            return
        if not isinstance(audio_id, str) or not audio_id:
            error.update("[red]Elegí un audio.[/red]")
            return
        if not prompt:
            error.update("[red]El prompt no puede estar vacío.[/red]")
            return
        if len(prompt) > _PROMPT_MAX_CHARS:
            error.update(f"[red]Prompt supera {_PROMPT_MAX_CHARS} caracteres.[/red]")
            return

        self.dismiss(NewVideoFormResult(image_id=image_id, audio_id=audio_id, prompt=prompt))

    async def _on_preview(self) -> None:
        """Reproduce el audio seleccionado para que el usuario lo verifique."""
        audio_select = self.query_one("#video-audio", Select)
        audio_id = audio_select.value
        if not isinstance(audio_id, str) or not audio_id:
            return
        # Buscamos el GeneratedAudio en la lista del modal (no hace HTTP).
        chosen = next((a for a in self._audios if a.id == audio_id), None)
        if chosen is None:
            return
        try:
            await self._audio_player.play_audio(chosen.kie_url)
        except (OSError, UrlValidationError):
            # Best-effort: si no se puede reproducir, no rompemos el form.
            return


def _image_options(images: list[UploadedImage]) -> list[tuple[str, str]]:
    """`(display, value)` para el Select de imágenes."""
    if not images:
        # Select de Textual no admite lista vacía: damos un placeholder.
        return [("(sin imágenes — usá la pantalla Imágenes primero)", "")]
    return [(f"{img.label}  ·  {_short(img.kie_file_path)}", img.id) for img in images]


def _audio_options(audios: list[GeneratedAudio]) -> list[tuple[str, str]]:
    if not audios:
        return [("(sin audios — usá la pantalla Audios primero)", "")]
    return [(f"{a.label}  ·  {_short(a.kie_file_path)}", a.id) for a in audios]


def _short(text: str, max_len: int = 36) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


# Helper para que el caller pueda construir un Input de Select sin
# importar Textual directamente (usado por tests). Sin uso runtime.
_ = Input
