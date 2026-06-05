"""Pantalla `Configuración`: API keys + endpoints + ejecución + defaults.

Solo dispatch + render (CR-10.1). Recibe `KeysController` y `SettingsController`
inyectados desde el composition root. No conoce `infra/`, ni `httpx`, ni
`aiosqlite`.

Notifica al composition root cambios que requieren reconstruir clientes
(key activa cambió o endpoints cambiaron) llamando a `on_kie_credentials_changed`
o `on_endpoints_changed` si fueron provistos.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import ClassVar, Final

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Static,
    TabbedContent,
    TabPane,
)

from ...app_layer.keys_controller import KeysController
from ...app_layer.settings_controller import SettingsController
from ...domain.errors import JobValidationError, KeyValidationError, KieError
from ...domain.models import KieKey
from .key_form import KeyFormResult, KeyFormScreen

NotifyAsync = Callable[[], Awaitable[None]]

_NOTIFICATION_TIMEOUT: Final[int] = 4
_LONG_NOTIFICATION_TIMEOUT: Final[int] = 6
_KEY_TABLE_COLUMNS: Final[tuple[str, ...]] = (
    "Activa",
    "ID",
    "Label",
    "Key (masked)",
    "Última validación",
    "Saldo",
)
# Umbral de "saldo bajo" para resaltar en rojo en la tabla de keys.
# 5 créditos es < 1 TTS multilingual-v2 (~10 cr observados): si esta key
# tiene menos, prácticamente cualquier llamada va a fallar con 402.
_LOW_CREDITS_THRESHOLD: Final[float] = 5.0


class SettingsScreen(Screen[None]):
    """Pantalla raíz de configuración."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "go_back", "Volver"),
    ]

    def __init__(
        self,
        keys_controller: KeysController,
        settings_controller: SettingsController,
        *,
        on_kie_credentials_changed: NotifyAsync | None = None,
        on_endpoints_changed: NotifyAsync | None = None,
    ) -> None:
        super().__init__()
        self._keys = keys_controller
        self._settings = settings_controller
        self._on_credentials_changed = on_kie_credentials_changed
        self._on_endpoints_changed = on_endpoints_changed

    # --- composición -------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        snapshot = self._settings.snapshot()
        with Vertical(id="settings-box"):
            yield Static("[b]Configuración[/b]", id="settings-title")
            with TabbedContent(initial="tab-keys"):
                with TabPane("API Keys", id="tab-keys"):
                    yield from self._compose_keys_tab()
                with TabPane("Endpoints", id="tab-endpoints"):
                    yield from self._compose_endpoints_tab(snapshot)
                with TabPane("Ejecución", id="tab-execution"):
                    yield from self._compose_execution_tab(snapshot)
                with TabPane("Defaults", id="tab-defaults"):
                    yield from self._compose_defaults_tab(snapshot)
            yield Static("", id="status-bar")
        yield Footer()

    def _compose_keys_tab(self) -> ComposeResult:
        table: DataTable[str] = DataTable(id="keys-table", cursor_type="row", zebra_stripes=True)
        for column in _KEY_TABLE_COLUMNS:
            table.add_column(column, key=column)
        yield table
        with Horizontal(classes="actions-row actions-row-keys"):
            yield Button("Agregar", id="key-add", variant="primary")
            yield Button("Activar", id="key-activate", classes="btn-info")
            yield Button("Probar", id="key-test", classes="btn-warning")
            yield Button("Eliminar", id="key-delete", variant="error")

    def _compose_endpoints_tab(self, snapshot) -> ComposeResult:  # type: ignore[no-untyped-def]
        with Vertical(classes="field-row"):
            yield Label("KIE_API_BASE")
            yield Input(value=snapshot.kie_api_base, id="kie-api-base")
            yield Label("KIE_UPLOAD_BASE")
            yield Input(value=snapshot.kie_upload_base, id="kie-upload-base")
        with Horizontal(classes="actions-row actions-row-save"):
            yield Button("Guardar endpoints", id="save-endpoints", variant="primary")

    def _compose_execution_tab(self, snapshot) -> ComposeResult:  # type: ignore[no-untyped-def]
        with Vertical(classes="field-row"):
            yield Label("MAX_PARALLEL_JOBS")
            yield Input(value=str(snapshot.max_parallel_jobs), id="max-parallel")
            yield Label("POLL_INTERVAL_SECONDS")
            yield Input(value=str(snapshot.poll_interval_seconds), id="poll-interval")
            yield Label("TASK_TIMEOUT_SECONDS")
            yield Input(value=str(snapshot.task_timeout_seconds), id="task-timeout")
        with Horizontal(classes="actions-row actions-row-save"):
            yield Button("Guardar ejecución", id="save-execution", variant="primary")

    def _compose_defaults_tab(self, snapshot) -> ComposeResult:  # type: ignore[no-untyped-def]
        with Vertical(classes="field-row"):
            yield Label("DEFAULT_VOICE")
            yield Input(value=snapshot.default_voice, id="default-voice")
            yield Label("DEFAULT_PROMPT")
            yield Input(value=snapshot.default_prompt, id="default-prompt")
        with Horizontal(classes="actions-row actions-row-save"):
            yield Button("Guardar defaults", id="save-defaults", variant="primary")

    # --- ciclo de vida -----------------------------------------------------

    async def on_mount(self) -> None:
        await self._refresh_keys_table()

    # --- handlers ----------------------------------------------------------

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        handler = _BUTTON_HANDLERS.get(button_id)
        if handler is None:
            return
        await handler(self)

    def action_go_back(self) -> None:
        self.app.pop_screen()

    # --- API Keys handlers ------------------------------------------------

    async def _handle_add_key(self) -> None:
        """Abre el modal en background. Cuando el usuario cierra, se invoca
        `_on_key_form_dismissed` (push_screen con callback no requiere
        @work, a diferencia de push_screen_wait — ver
        https://textual.textualize.io/api/app/#textual.app.App.push_screen).
        """
        self.app.push_screen(KeyFormScreen(), self._on_key_form_dismissed)

    def _on_key_form_dismissed(self, result: KeyFormResult | None) -> None:
        if result is None:
            return
        # El callback es síncrono; el persist requiere await, así que lo
        # disparamos como task vigilada por el exception handler global.
        self.app.run_worker(self._persist_new_key(result), exclusive=False)

    async def _persist_new_key(self, payload: KeyFormResult) -> None:
        try:
            await self._keys.add_key(payload.id, payload.label, payload.key)
        except KeyValidationError as exc:
            self._set_status(f"✖ {exc}", error=True)
            return
        self._set_status(f"✓ key '{payload.label}' agregada")
        await self._refresh_keys_table()

    async def _handle_activate_key(self) -> None:
        key_id = self._selected_key_id()
        if key_id is None:
            self._set_status("Selecciona una key en la tabla primero", error=True)
            return
        try:
            await self._keys.set_active(key_id)
        except KieError as exc:
            self._set_status(f"✖ {exc}", error=True)
            return
        self._set_status(f"✓ key '{key_id}' activada")
        await self._refresh_keys_table()
        await self._notify_credentials_changed()

    async def _handle_delete_key(self) -> None:
        key_id = self._selected_key_id()
        if key_id is None:
            self._set_status("Selecciona una key en la tabla primero", error=True)
            return
        try:
            await self._keys.delete_key(key_id)
        except KieError as exc:
            self._set_status(f"✖ {exc}", error=True)
            return
        self._set_status(f"✓ key '{key_id}' eliminada")
        await self._refresh_keys_table()

    async def _handle_test_key(self) -> None:
        key_id = self._selected_key_id()
        if key_id is None:
            self._set_status("Selecciona una key en la tabla primero", error=True)
            return
        self._set_status(f"… probando '{key_id}' contra Kie")
        try:
            tested = await self._keys.test_key(key_id)
        except KieError as exc:
            self._set_status(f"✖ {exc}", error=True)
            return
        message = _format_test_result(tested)
        self._set_status(message, error=tested.last_validated_status != "ok")
        await self._refresh_keys_table()

    # --- Endpoints / Execution / Defaults handlers ------------------------

    async def _handle_save_endpoints(self) -> None:
        api_base = self.query_one("#kie-api-base", Input).value
        upload_base = self.query_one("#kie-upload-base", Input).value
        try:
            self._settings.update_endpoints(api_base, upload_base)
        except JobValidationError as exc:
            self._set_status(f"✖ {exc}", error=True)
            return
        self._set_status("✓ endpoints guardados en .env")
        await self._notify_endpoints_changed()

    async def _handle_save_execution(self) -> None:
        try:
            max_parallel = int(self.query_one("#max-parallel", Input).value)
            poll = int(self.query_one("#poll-interval", Input).value)
            timeout = int(self.query_one("#task-timeout", Input).value)
        except ValueError:
            self._set_status("✖ los valores deben ser enteros", error=True)
            return
        try:
            self._settings.update_execution(max_parallel, poll, timeout)
        except JobValidationError as exc:
            self._set_status(f"✖ {exc}", error=True)
            return
        self._set_status(
            "✓ ejecución guardada en .env (reiniciá la app para aplicar el paralelismo)"
        )

    async def _handle_save_defaults(self) -> None:
        voice = self.query_one("#default-voice", Input).value
        prompt = self.query_one("#default-prompt", Input).value
        try:
            self._settings.update_defaults(voice, prompt)
        except JobValidationError as exc:
            self._set_status(f"✖ {exc}", error=True)
            return
        self._set_status("✓ defaults guardados en .env")

    # --- helpers internos -------------------------------------------------

    async def _refresh_keys_table(self) -> None:
        table = self.query_one("#keys-table", DataTable)
        table.clear()
        active = await self._keys.get_active()
        active_id = active.id if active else None
        for key in await self._keys.list_keys():
            table.add_row(
                "●" if key.id == active_id else "",
                key.id,
                key.label,
                key.masked(),
                _format_validation_cell(key),
                _format_credits_cell(key),
                key=key.id,
            )

    def _selected_key_id(self) -> str | None:
        table = self.query_one("#keys-table", DataTable)
        if table.row_count == 0:
            return None
        try:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        except Exception:
            return None
        return row_key.value

    def _set_status(self, message: str, *, error: bool = False) -> None:
        bar = self.query_one("#status-bar", Static)
        bar.update(f"[red]{message}[/red]" if error else message)
        timeout = _LONG_NOTIFICATION_TIMEOUT if error else _NOTIFICATION_TIMEOUT
        self.notify(message, severity="error" if error else "information", timeout=timeout)

    async def _notify_credentials_changed(self) -> None:
        if self._on_credentials_changed is not None:
            await self._on_credentials_changed()

    async def _notify_endpoints_changed(self) -> None:
        if self._on_endpoints_changed is not None:
            await self._on_endpoints_changed()


def _format_validation_cell(key: KieKey) -> str:
    if key.last_validated_status is None or key.last_validated_at is None:
        return "—"
    when = key.last_validated_at.strftime("%Y-%m-%d %H:%M")
    glyph = {"ok": "✓", "unauthorized": "✖ 401", "error": "✖"}.get(key.last_validated_status, "?")
    return f"{glyph} {when}"


def _format_credits_cell(key: KieKey) -> str:
    """Formato del saldo en la tabla de keys.

    Devuelve "—" si nunca se midió, y rojo si es bajo (≤ 5 cr) para alertar
    al usuario antes de que un job falle con 402.
    """
    if key.last_known_credits is None:
        return "—"
    if key.last_known_credits <= _LOW_CREDITS_THRESHOLD:
        return f"[red]{key.last_known_credits:.2f} cr[/red]"
    return f"{key.last_known_credits:.2f} cr"


def _format_test_result(key: KieKey) -> str:
    status = key.last_validated_status
    if status == "ok":
        credits_suffix = (
            f" · saldo {key.last_known_credits:.2f} cr"
            if key.last_known_credits is not None
            else ""
        )
        return f"✓ '{key.label}' validada contra Kie{credits_suffix}"
    if status == "unauthorized":
        return f"✖ '{key.label}' rechazada por Kie (401/403)"
    return f"✖ '{key.label}' no se pudo validar (error de red o servidor)"


_BUTTON_HANDLERS: dict[str, Callable[[SettingsScreen], Awaitable[None]]] = {
    "key-add": SettingsScreen._handle_add_key,
    "key-activate": SettingsScreen._handle_activate_key,
    "key-delete": SettingsScreen._handle_delete_key,
    "key-test": SettingsScreen._handle_test_key,
    "save-endpoints": SettingsScreen._handle_save_endpoints,
    "save-execution": SettingsScreen._handle_save_execution,
    "save-defaults": SettingsScreen._handle_save_defaults,
}
