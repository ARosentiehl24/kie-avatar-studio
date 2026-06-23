from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import ClassVar, Final

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import (
    Button,
    DataTable,
    Input,
    Static,
)

from ...app_layer.keys_controller import KeysController
from ...app_layer.settings_controller import SettingsController
from ...domain.errors import JobValidationError, KeyValidationError, KieError
from .._icons import ERROR, OK
from ._settings_widgets import (
    compose_settings_layout,
    format_credits_cell,
    format_test_result,
    format_validation_cell,
)
from .key_form import KeyFormResult, KeyFormScreen

NotifyAsync = Callable[[], Awaitable[None]]
CleanupAsync = Callable[[], Awaitable[str]]

_NOTIFICATION_TIMEOUT: Final[int] = 4
_LONG_NOTIFICATION_TIMEOUT: Final[int] = 6


class SettingsScreen(Screen[None]):
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
        on_integrations_changed: NotifyAsync | None = None,
        on_runtime_cleanup: CleanupAsync | None = None,
    ) -> None:
        super().__init__()
        self._keys = keys_controller
        self._settings = settings_controller
        self._on_credentials_changed = on_kie_credentials_changed
        self._on_endpoints_changed = on_endpoints_changed
        self._on_integrations_changed = on_integrations_changed
        self._on_runtime_cleanup = on_runtime_cleanup
        self._cleanup_confirm_pending = False

    def compose(self) -> ComposeResult:
        yield from compose_settings_layout(self._settings.snapshot())

    async def on_mount(self) -> None:
        await self._refresh_keys_table()
        await self._refresh_integrations_form()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        handler = _BUTTON_HANDLERS.get(button_id)
        if handler is None:
            return
        await handler(self)

    def action_go_back(self) -> None:
        self.app.pop_screen()

    async def _handle_add_key(self) -> None:
        self.app.push_screen(KeyFormScreen(), self._on_key_form_dismissed)

    def _on_key_form_dismissed(self, result: KeyFormResult | None) -> None:
        if result is None:
            return
        self.app.run_worker(self._persist_new_key(result), exclusive=False)

    async def _persist_new_key(self, payload: KeyFormResult) -> None:
        try:
            await self._keys.add_key(payload.id, payload.label, payload.key)
        except KeyValidationError as exc:
            self._set_status(f"{ERROR} {exc}", error=True)
            return
        self._set_status(f"{OK} key '{payload.label}' agregada")
        await self._refresh_keys_table()

    async def _handle_activate_key(self) -> None:
        key_id = self._selected_key_id()
        if key_id is None:
            self._set_status("Selecciona una key en la tabla primero", error=True)
            return
        try:
            await self._keys.set_active(key_id)
        except KieError as exc:
            self._set_status(f"{ERROR} {exc}", error=True)
            return
        self._set_status(f"{OK} key '{key_id}' activada")
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
            self._set_status(f"{ERROR} {exc}", error=True)
            return
        self._set_status(f"{OK} key '{key_id}' eliminada")
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
            self._set_status(f"{ERROR} {exc}", error=True)
            return
        message = format_test_result(tested)
        self._set_status(message, error=tested.last_validated_status != "ok")
        await self._refresh_keys_table()

    async def _handle_save_endpoints(self) -> None:
        api_base = self.query_one("#kie-api-base", Input).value
        upload_base = self.query_one("#kie-upload-base", Input).value
        try:
            self._settings.update_endpoints(api_base, upload_base)
        except JobValidationError as exc:
            self._set_status(f"{ERROR} {exc}", error=True)
            return
        self._set_status(f"{OK} endpoints guardados en .env")
        await self._notify_endpoints_changed()

    async def _handle_save_execution(self) -> None:
        try:
            max_parallel = int(self.query_one("#max-parallel", Input).value)
            poll = int(self.query_one("#poll-interval", Input).value)
            timeout = int(self.query_one("#task-timeout", Input).value)
        except ValueError:
            self._set_status(f"{ERROR} los valores deben ser enteros", error=True)
            return
        try:
            self._settings.update_execution(max_parallel, poll, timeout)
        except JobValidationError as exc:
            self._set_status(f"{ERROR} {exc}", error=True)
            return
        self._set_status(
            f"{OK} ejecución guardada en .env (reiniciá la app para aplicar el paralelismo)"
        )

    async def _handle_save_concurrency(self) -> None:
        try:
            audio = int(self.query_one("#max-parallel-audio", Input).value)
            image = int(self.query_one("#max-parallel-image", Input).value)
            video = int(self.query_one("#max-parallel-video", Input).value)
            upload = int(self.query_one("#max-parallel-upload", Input).value)
            download = int(self.query_one("#max-parallel-download", Input).value)
        except ValueError:
            self._set_status(f"{ERROR} los valores deben ser enteros", error=True)
            return
        try:
            self._settings.update_concurrency(
                audio=audio,
                image=image,
                video=video,
                upload=upload,
                download=download,
            )
        except JobValidationError as exc:
            self._set_status(f"{ERROR} {exc}", error=True)
            return
        self._set_status(
            f"{OK} concurrencia guardada en .env (reiniciá la app para aplicar los límites)"
        )

    async def _handle_save_defaults(self) -> None:
        voice = self.query_one("#default-voice", Input).value
        prompt = self.query_one("#default-prompt", Input).value
        try:
            self._settings.update_defaults(voice, prompt)
        except JobValidationError as exc:
            self._set_status(f"{ERROR} {exc}", error=True)
            return
        self._set_status(f"{OK} defaults guardados en .env")

    async def _handle_save_integrations(self) -> None:
        elevenlabs_api_key = self.query_one("#elevenlabs-api-key", Input).value
        await self._keys.set_elevenlabs_api_key(elevenlabs_api_key)
        self._set_status(f"{OK} integraciones guardadas en data/keys.json")
        await self._notify_integrations_changed()

    async def _handle_cleanup_runtime_db(self) -> None:
        if self._on_runtime_cleanup is None:
            self._set_status(f"{ERROR} limpieza no disponible en esta sesión", error=True)
            return
        if not self._cleanup_confirm_pending:
            self._cleanup_confirm_pending = True
            self._set_status(
                f"{ERROR} presioná 'Limpiar DB runtime' otra vez para confirmar; "
                "keys y outputs se conservan",
                error=True,
            )
            return
        self._cleanup_confirm_pending = False
        try:
            message = await self._on_runtime_cleanup()
        except JobValidationError as exc:
            self._set_status(f"{ERROR} {exc}", error=True)
            return
        self._set_status(f"{OK} {message}")

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
                format_validation_cell(key),
                format_credits_cell(key),
                key=key.id,
            )

    async def _refresh_integrations_form(self) -> None:
        stored = await self._keys.get_elevenlabs_api_key()
        value = stored if stored is not None else self._settings.snapshot().elevenlabs_api_key
        self.query_one("#elevenlabs-api-key", Input).value = value

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
        if not message.startswith(f"{ERROR} presioná 'Limpiar DB runtime'"):
            self._cleanup_confirm_pending = False
        bar.update(f"[red]{message}[/red]" if error else message)
        timeout = _LONG_NOTIFICATION_TIMEOUT if error else _NOTIFICATION_TIMEOUT
        self.notify(message, severity="error" if error else "information", timeout=timeout)

    async def _notify_credentials_changed(self) -> None:
        if self._on_credentials_changed is not None:
            await self._on_credentials_changed()

    async def _notify_endpoints_changed(self) -> None:
        if self._on_endpoints_changed is not None:
            await self._on_endpoints_changed()

    async def _notify_integrations_changed(self) -> None:
        if self._on_integrations_changed is not None:
            await self._on_integrations_changed()


_BUTTON_HANDLERS: dict[str, Callable[[SettingsScreen], Awaitable[None]]] = {
    "key-add": SettingsScreen._handle_add_key,
    "key-activate": SettingsScreen._handle_activate_key,
    "key-delete": SettingsScreen._handle_delete_key,
    "key-test": SettingsScreen._handle_test_key,
    "save-endpoints": SettingsScreen._handle_save_endpoints,
    "save-execution": SettingsScreen._handle_save_execution,
    "save-concurrency": SettingsScreen._handle_save_concurrency,
    "save-defaults": SettingsScreen._handle_save_defaults,
    "save-integrations": SettingsScreen._handle_save_integrations,
    "cleanup-runtime-db": SettingsScreen._handle_cleanup_runtime_db,
}
