"""Modal para crear un `VideoJob` desde assets ya en Kie (Modo B).

Selects de imagen + audio (poblados desde los stores locales) +
TextArea para el prompt + botones Cancelar / Generar. Solo dispatch
+ render (CR-10.1): la validación de assets expirados y la
construcción del `VideoJob` viven en `VideosController`.

El selector de imagen acepta tanto `UploadedImage` (TTL 24h) como
`GeneratedImage` (TTL 14d) — el listado se construye en
`ImageCatalogController.list_usable_assets()` y llega acá como
`list[ImageAssetRef]`. El value del Select usa un id sintético
`"kind:id"` para que dos refs con el mismo id (uploaded + generated)
sean distinguibles. Al dismiss, el form devuelve el `ImageAssetRef`
completo (no el id) para que el controller resuelva contra el store
correcto.

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
from textual.widgets import Button, Label, Select, Static, TextArea

from ...app_layer.audio_player import AudioPlayer
from ...domain.errors import UrlValidationError
from ...domain.models import GeneratedAudio, ImageAssetKind, ImageAssetRef

_FORM_TITLE: Final[str] = "Generar video desde imagen + audio existentes"
_PROMPT_MAX_CHARS: Final[int] = 5000
_NO_ASSETS_MSG: Final[str] = (
    "No hay {kind} cargados todavía. Primero usá la pantalla '{screen}' para crear/cargar."
)


@dataclass(frozen=True, slots=True)
class NewVideoFormResult:
    """Payload devuelto cuando el usuario confirma el form.

    `image_ref` es un DTO discriminado (`uploaded` o `generated`) para
    que el caller resuelva contra el store correcto. `audio_id` sigue
    siendo string porque actualmente solo hay un store de audio.
    """

    image_ref: ImageAssetRef
    audio_id: str
    prompt: str


class NewVideoFormScreen(ModalScreen[NewVideoFormResult | None]):
    """Modal con Select imagen + Select audio + TextArea prompt."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "cancel", "Cancelar", show=False),
    ]

    def __init__(
        self,
        image_refs: list[ImageAssetRef],
        audios: list[GeneratedAudio],
        audio_player: AudioPlayer,
    ) -> None:
        super().__init__()
        self._image_refs = image_refs
        self._audios = audios
        self._audio_player = audio_player
        # Index para resolver rápido el ref desde el value del Select.
        self._refs_by_select_value: dict[str, ImageAssetRef] = {
            _select_value(ref): ref for ref in image_refs
        }

    def compose(self) -> ComposeResult:
        # Mismo patrón que el modal Generar Audio: outer Vertical (no
        # scroll) + body scrollable + footer sticky. Evita que el
        # scrollbar atraviese los botones de acción.
        with Vertical(id="video-form-dialog"):
            with VerticalScroll(id="video-form-body"):
                yield Static(_FORM_TITLE, id="video-form-title")
                yield Label("Imagen (subida o generada con Nano Banana)")
                yield Select(
                    options=_image_options(self._image_refs),
                    allow_blank=not self._image_refs,
                    id="video-image",
                )
                if not self._image_refs:
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
                    yield Button("Preview", id="video-audio-preview", classes="btn-info")
                    yield Button("Detener", id="video-audio-stop", classes="btn-warning")
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
        if self._image_refs:
            select = self.query_one("#video-image", Select)
            select.value = _select_value(self._image_refs[0])
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

        image_value = image_select.value
        audio_id = audio_select.value
        prompt = prompt_area.text.strip()

        if not isinstance(image_value, str) or not image_value:
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

        ref = self._refs_by_select_value.get(image_value)
        if ref is None:
            # El catálogo cambió mientras el modal estaba abierto y el
            # valor del select ya no apunta a un ref vivo. Pedimos al
            # usuario reabrir el modal en vez de encolar algo inválido.
            error.update("[red]Esa imagen ya no está disponible. Cerrá y reabrí el form.[/red]")
            return

        self.dismiss(NewVideoFormResult(image_ref=ref, audio_id=audio_id, prompt=prompt))

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


def _select_value(ref: ImageAssetRef) -> str:
    """Encodea (kind, id) en un único string usable como value del Select.

    Necesario porque dos refs pueden tener mismo `id` si vienen de stores
    distintos (uploaded vs generated). Usamos `:` como separador porque
    los ids generados por la app no contienen `:` (timestamps + hex).
    """
    return f"{ref.kind.value}:{ref.id}"


def _kind_badge(kind: ImageAssetKind) -> str:
    """Etiqueta corta para el dropdown que aclara origen de la imagen."""
    if kind == ImageAssetKind.UPLOADED:
        return "[subida]"
    return "[generada]"


def _image_options(refs: list[ImageAssetRef]) -> list[tuple[str, str]]:
    """`(display, value)` para el Select de imágenes."""
    if not refs:
        return [("(sin imágenes — usá la pantalla Imágenes primero)", "")]
    return [
        (f"{_kind_badge(ref.kind)} {ref.label}  ·  {_short(ref.kie_url)}", _select_value(ref))
        for ref in refs
    ]


def _audio_options(audios: list[GeneratedAudio]) -> list[tuple[str, str]]:
    if not audios:
        return [("(sin audios — usá la pantalla Audios primero)", "")]
    return [(f"{a.label}  ·  {_short(a.kie_file_path)}", a.id) for a in audios]


def _short(text: str, max_len: int = 36) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"
