"""Modal para generar un nuevo audio TTS vía Kie.

Solo dispatch + render (CR-10.1). Valida sintaxis localmente con `policies`
y devuelve `GenerateAudioFormResult` vía `dismiss(...)`. La generación HTTP
+ persistencia corren en el caller (`AudiosScreen`), no acá.

El layout usa `VerticalScroll` para que el modal sea navegable cuando la
ventana es chica (todos los campos + la sección "Avanzado" no entran en
35 filas de alto).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Final

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Collapsible, Input, Label, Select, Static, TextArea

from ...app_layer.audio_player import AudioPlayer
from ...domain.errors import (
    AudioValidationError,
    UrlValidationError,
    VoiceSettingsValidationError,
)
from ...domain.kie_voice_catalog import BUILTIN_VOICES, get_builtin_voice
from ...domain.models import VoicePreset, VoiceSettings
from ...domain.policies import (
    MAX_SCRIPT_CHARS,
    validate_tts_script,
    validate_voice_id,
    validate_voice_settings,
)

_FORM_TITLE: Final[str] = "Generar audio TTS (ElevenLabs vía Kie)"


@dataclass(frozen=True, slots=True)
class GenerateAudioFormResult:
    """Payload devuelto cuando el usuario confirma el form.

    `keep_open` lo seteamos a `True` cuando el usuario apreto "Generar y otro":
    el caller debe encolar la generacion en background y reabrir el modal
    pre-cargando voz + voice_settings para que el flujo de generar muchos
    audios con la misma configuracion sea fluido.

    `save_as_preset_label` se setea cuando el usuario apretó "Guardar
    preset" en lugar de "Generar". El caller debe crear el VoicePreset
    con la config actual y reabrir el modal (en lugar de encolar
    generación).
    """

    label: str
    script: str
    voice_id: str
    voice_settings: VoiceSettings | None
    keep_open: bool = False
    save_as_preset_label: str | None = None


@dataclass(frozen=True, slots=True)
class GenerateAudioFormDefaults:
    """Valores pre-cargados cuando el modal se reabre tras 'Generar y otro'.

    Solo arrastramos voice + settings; label y script siempre arrancan
    vacios para forzar input nuevo (sino el usuario podria duplicar audios
    sin querer).
    """

    voice_id: str
    voice_settings: VoiceSettings | None


def _format_preset_summary(preset: VoicePreset) -> str:
    """Resumen compacto del preset para mostrar en el Select.

    Ej.: 'James  ·  sta=0.5 · spd=1.1' o 'James  ·  defaults Kie'.
    Hace claro de un vistazo qué configuración va a precargar.
    """
    voice = get_builtin_voice(preset.voice_id)
    voice_label = voice.label if voice is not None else preset.voice_id
    if preset.voice_settings is None:
        return f"{voice_label} · defaults"
    s = preset.voice_settings
    parts: list[str] = [voice_label]
    if s.stability is not None:
        parts.append(f"sta={s.stability}")
    if s.speed is not None:
        parts.append(f"spd={s.speed}")
    return " · ".join(parts)


class GenerateAudioFormScreen(ModalScreen[GenerateAudioFormResult | None]):
    """Modal con label + script + voice + voice_settings avanzados opcionales.

    Si se pasa `presets` (lista de VoicePreset disponibles), agrega arriba
    un Select 'Cargar preset' que al seleccionar uno precarga voice + los
    5 sliders avanzados. También agrega un botón 'Guardar como preset'
    en el footer que devuelve el resultado con `save_as_preset=True`
    para que el caller cree un preset nuevo con la config actual.
    """

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "cancel", "Cancelar", show=False),
    ]

    def __init__(
        self,
        audio_player: AudioPlayer,
        defaults: GenerateAudioFormDefaults | None = None,
        presets: list[VoicePreset] | None = None,
    ) -> None:
        super().__init__()
        self._audio_player = audio_player
        self._defaults = defaults
        self._presets = presets or []

    def compose(self) -> ComposeResult:
        # Contenedor exterior (Vertical, no scroll): aloja el body
        # scrollable y los botones de acción fijos al pie. Sacar los
        # botones del scroll evita que el scrollbar los atraviese y que
        # el "Generar" se vea cortado cuando el contenido excede el alto.
        with Vertical(id="audio-form-dialog"):
            with VerticalScroll(id="audio-form-body"):
                yield Static(_FORM_TITLE, id="audio-form-title")

                # Select de presets (solo si hay alguno). El placeholder
                # "(elegí preset…)" indica que es opcional — el usuario
                # puede configurar todo manualmente sin cargar ninguno.
                if self._presets:
                    yield Label("Cargar preset de voz (opcional)")
                    yield Select(
                        options=[
                            (f"{p.label}  ·  {_format_preset_summary(p)}", p.id)
                            for p in self._presets
                        ],
                        prompt="(elegí un preset…)",
                        allow_blank=True,
                        id="audio-preset",
                    )

                yield Label("Label legible (ej. 'intro saludo')")
                yield Input(placeholder="intro saludo", id="audio-label")
                yield Label(f"Script a sintetizar (máx {MAX_SCRIPT_CHARS} chars)")
                yield TextArea(id="audio-script", language=None)
                yield Static(f"0 / {MAX_SCRIPT_CHARS}", id="audio-script-counter")
                yield Label("Voz (catálogo built-in de Kie — 67 voces)")
                with Horizontal(id="audio-voice-row"):
                    yield Select(
                        options=[(voice.display_name, voice.voice_id) for voice in BUILTIN_VOICES],
                        value=self._initial_voice_id(),
                        allow_blank=False,
                        id="audio-voice",
                    )
                    yield Button("Preview", id="audio-preview", classes="btn-info")
                    yield Button("Detener", id="audio-preview-stop", classes="btn-warning")
                with Collapsible(title="Avanzado — voice settings", id="audio-advanced"):
                    yield Label("stability (0.0 - 1.0, vacío = default 0.5)")
                    yield Input(
                        placeholder="0.5",
                        id="audio-stability",
                        value=self._initial("stability"),
                    )
                    yield Label("similarity_boost (0.0 - 1.0, vacío = default 0.75)")
                    yield Input(
                        placeholder="0.75",
                        id="audio-similarity",
                        value=self._initial("similarity_boost"),
                    )
                    yield Label("style (0.0 - 1.0, vacío = default 0)")
                    yield Input(placeholder="0", id="audio-style", value=self._initial("style"))
                    yield Label("speed (0.7 - 1.2, vacío = default 1.0)")
                    yield Input(
                        placeholder="1.0",
                        id="audio-speed",
                        value=self._initial("speed"),
                    )
                    yield Label("language_code ISO 639-1 (vacío = auto; solo turbo/flash v2.5)")
                    yield Input(
                        placeholder="es",
                        id="audio-language",
                        value=self._initial_language_code(),
                    )
                yield Static("", id="audio-form-error")
            with Horizontal(id="audio-form-footer"):
                yield Button("Cancelar", id="cancel", variant="default")
                # "Guardar como preset" solo aparece si presets están
                # habilitados — sino el botón no tendría caller posible.
                if self._presets is not None:
                    yield Button("Guardar preset", id="save-preset", classes="btn-info")
                yield Button("Generar y otro", id="generate-more", classes="btn-info")
                yield Button("Generar", id="generate", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#audio-label", Input).focus()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id == "audio-script":
            count = len(event.text_area.text)
            counter = self.query_one("#audio-script-counter", Static)
            over = count > MAX_SCRIPT_CHARS
            counter.update(
                f"[red]{count} / {MAX_SCRIPT_CHARS}[/red]"
                if over
                else f"{count} / {MAX_SCRIPT_CHARS}"
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "cancel":
            self.action_cancel()
        elif button_id == "generate":
            self._submit(keep_open=False)
        elif button_id == "generate-more":
            self._submit(keep_open=True)
        elif button_id == "save-preset":
            self._submit_save_preset()
        elif button_id == "audio-preview":
            self._handle_preview()
        elif button_id == "audio-preview-stop":
            self._handle_preview_stop()

    def on_select_changed(self, event: Select.Changed) -> None:
        """Si el usuario eligió un preset, precarga voice + 5 sliders.

        Solo escuchamos cambios del Select `#audio-preset`. Cuando hay
        un preset seleccionado, sobreescribimos los inputs avanzados
        con sus valores. Si el preset NO tenía algún campo (None), el
        Input correspondiente queda en su valor anterior — el usuario
        puede seguir ajustándolo a mano.
        """
        if event.select.id != "audio-preset":
            return
        preset_id = event.value
        if not isinstance(preset_id, str) or not preset_id:
            return
        preset = next((p for p in self._presets if p.id == preset_id), None)
        if preset is None:
            return
        # Voice id principal.
        self.query_one("#audio-voice", Select).value = preset.voice_id
        # 5 sliders avanzados (vacío si el preset no los tenía).
        settings = preset.voice_settings
        self.query_one("#audio-stability", Input).value = _opt_str(
            settings.stability if settings else None
        )
        self.query_one("#audio-similarity", Input).value = _opt_str(
            settings.similarity_boost if settings else None
        )
        self.query_one("#audio-style", Input).value = _opt_str(settings.style if settings else None)
        self.query_one("#audio-speed", Input).value = _opt_str(settings.speed if settings else None)
        self.query_one("#audio-language", Input).value = (
            settings.language_code if settings and settings.language_code else ""
        )
        self._set_error(
            f"[dim]✅ preset '{preset.label}' cargado (podés ajustar antes de generar)[/dim]"
        )

    def action_cancel(self) -> None:
        # Cancelar el modal también detiene el preview en curso: si el
        # usuario se va sin generar nada, esperaría que el audio pare.
        self.app.run_worker(self._audio_player.stop(), exclusive=False)
        self.dismiss(None)

    # --- handlers ---------------------------------------------------------

    def _handle_preview(self) -> None:
        """Reproduce el preview de la voz seleccionada (auto-cancela el anterior)."""
        voice_id = self._selected_voice_id()
        if voice_id is None:
            self._set_error("Seleccioná una voz primero")
            return
        voice = get_builtin_voice(voice_id)
        if voice is None:
            self._set_error(f"voice_id {voice_id!r} no está en el catálogo built-in")
            return
        self.app.run_worker(self._open_preview(voice.preview_url), exclusive=False)

    def _handle_preview_stop(self) -> None:
        """Detiene la reproducción del preview en curso. Idempotente."""
        self.app.run_worker(self._audio_player.stop(), exclusive=False)

    async def _open_preview(self, url: str) -> None:
        try:
            await self._audio_player.play_voice_preview(url)
        except (OSError, UrlValidationError) as exc:
            self._set_error(f"no pude reproducir el preview: {exc}")

    def _submit(self, *, keep_open: bool) -> None:
        label = self.query_one("#audio-label", Input).value
        script = self.query_one("#audio-script", TextArea).text
        voice_id = self._selected_voice_id() or ""
        try:
            self._validate_label(label)
            validate_tts_script(script)
            validate_voice_id(voice_id, allow_custom=False)
            settings = self._collect_voice_settings()
            if settings is not None:
                validate_voice_settings(settings)
        except (AudioValidationError, VoiceSettingsValidationError) as exc:
            self._set_error(str(exc))
            return
        # Para que el usuario no se quede con un preview sonando mientras la
        # generación corre. La generación puede tardar varios segundos.
        self.app.run_worker(self._audio_player.stop(), exclusive=False)
        self.dismiss(
            GenerateAudioFormResult(
                label=label.strip(),
                script=script,
                voice_id=voice_id,
                voice_settings=settings,
                keep_open=keep_open,
            )
        )

    def _submit_save_preset(self) -> None:
        """Devuelve un Result marcando que el usuario quiere guardar la
        configuración actual como preset (en lugar de generar audio).

        Reusa el `label` del form como nombre del preset — semánticamente
        coherente ('intro saludo' como label de audio sirve también como
        nombre de preset). El script NO se persiste en el preset (los
        presets son configuración reusable, no contenido).

        El caller (AudiosScreen) recibe el Result con
        `save_as_preset_label` poblado, crea el VoicePreset via el
        controller, y reabre este modal con el nuevo preset disponible
        en el Select.
        """
        label = self.query_one("#audio-label", Input).value.strip()
        voice_id = self._selected_voice_id() or ""
        try:
            # Validamos solo lo necesario para un preset (no el script).
            self._validate_label(label)
            validate_voice_id(voice_id, allow_custom=False)
            settings = self._collect_voice_settings()
            if settings is not None:
                validate_voice_settings(settings)
        except (AudioValidationError, VoiceSettingsValidationError) as exc:
            self._set_error(str(exc))
            return
        self.app.run_worker(self._audio_player.stop(), exclusive=False)
        self.dismiss(
            GenerateAudioFormResult(
                label=label,
                script="",  # no se usa al guardar preset
                voice_id=voice_id,
                voice_settings=settings,
                keep_open=False,
                save_as_preset_label=label,
            )
        )

    # --- internals --------------------------------------------------------

    def _selected_voice_id(self) -> str | None:
        select = self.query_one("#audio-voice", Select)
        value = select.value
        if value is Select.BLANK or not isinstance(value, str):
            return None
        return value

    def _initial_voice_id(self) -> str:
        """Voice seleccionado al abrir el modal (default o pre-cargado)."""
        if self._defaults is not None:
            return self._defaults.voice_id
        return BUILTIN_VOICES[0].voice_id

    def _initial(self, field: str) -> str:
        """Valor inicial de los inputs numéricos avanzados.

        Si hay `defaults` y el campo está seteado, devuelve su string.
        Si no, devuelve "" para que el placeholder muestre el default Kie.
        """
        if self._defaults is None or self._defaults.voice_settings is None:
            return ""
        value = getattr(self._defaults.voice_settings, field, None)
        return "" if value is None else str(value)

    def _initial_language_code(self) -> str:
        if self._defaults is None or self._defaults.voice_settings is None:
            return ""
        return self._defaults.voice_settings.language_code or ""

    @staticmethod
    def _validate_label(label: str) -> None:
        if not label.strip():
            raise AudioValidationError("el label no puede estar vacío")

    def _collect_voice_settings(self) -> VoiceSettings | None:
        """Lee los 5 inputs avanzados y devuelve `VoiceSettings` o `None`.

        Si todos los campos están vacíos, devuelve `None` (no se mandan settings
        y Kie aplica los defaults documentados). Si alguno tiene valor, lo
        parsea respetando los rangos de Pydantic (`Field`).
        """
        stability = self._parse_float_input("#audio-stability", "stability")
        similarity = self._parse_float_input("#audio-similarity", "similarity_boost")
        style = self._parse_float_input("#audio-style", "style")
        speed = self._parse_float_input("#audio-speed", "speed")
        language_code = self.query_one("#audio-language", Input).value.strip() or None
        if all(v is None for v in (stability, similarity, style, speed, language_code)):
            return None
        try:
            return VoiceSettings(
                stability=stability,
                similarity_boost=similarity,
                style=style,
                speed=speed,
                language_code=language_code,
            )
        except ValueError as exc:
            raise VoiceSettingsValidationError(str(exc)) from exc

    def _parse_float_input(self, selector: str, field_name: str) -> float | None:
        raw = self.query_one(selector, Input).value.strip()
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError as exc:
            raise VoiceSettingsValidationError(
                f"{field_name} debe ser numérico (recibí: {raw!r})"
            ) from exc

    def _set_error(self, message: str) -> None:
        self.query_one("#audio-form-error", Static).update(f"[red]{message}[/red]")


def _opt_str(value: float | None) -> str:
    """Convierte un float opcional a string para precargar un Input.

    Helper para el handler `on_select_changed` cuando el usuario carga
    un preset: si el preset NO tenía valor, devolvemos "" para que el
    placeholder del Input siga mostrando el default Kie.
    """
    return "" if value is None else str(value)
