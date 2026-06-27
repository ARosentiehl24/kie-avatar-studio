"""Pantalla principal `Automatización`: workflows JSON declarativos.

Lista los archivos detectados en `workflows/` (merge con DB de runs
históricos) y ofrece acciones para encolar, ver progreso, reintentar y
cancelar. Solo dispatch + render (CR-10.1).

Para el detalle por workflow (steps individuales, progress granular)
abre `WorkflowDetailScreen` desde el botón "Ver detalle".
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import ClassVar, Final

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Static

from ...app_layer.workflow_controller import WorkflowController
from ...domain.errors import (
    ImageValidationError,
    KieError,
    WorkflowNotFoundError,
    WorkflowStepError,
    WorkflowValidationError,
)
from ...domain.events import WorkflowJobUpdated
from ...domain.models import (
    ImageAssetRef,
    ImageGenerationSettings,
    ModelCreationMethod,
    ProductImage,
    SceneApprovalMode,
    VoiceChangerSettings,
    WorkflowEntry,
    WorkflowJob,
    WorkflowPreSettings,
    WorkflowStatus,
)
from ...domain.ports import AudioPreviewPlayer, ElevenLabsVoicesClient
from .._counters import format_full_counters
from .._icons import ERROR, OK
from .._table_helpers import get_selected_row_key, select_row_by_key
from .._text_format import truncate
from ._workflow_format import (
    build_workflow_run_summary,
    format_warnings,
    format_workflow_status_cell,
)
from .configure_workflow import ConfigureResult, ConfigureWorkflowScreen
from .file_picker import ImageFilePickerScreen
from .preview_base_image import PreviewBaseImageScreen
from .scene_image_approval import SceneImageApprovalScreen
from .workflow_detail import WorkflowDetailScreen
from .workflow_summary import CreditsLoader, WorkflowSummaryScreen

_NOTIFICATION_TIMEOUT: Final[int] = 4
_LONG_NOTIFICATION_TIMEOUT: Final[int] = 6
_NAME_PREVIEW_LEN: Final[int] = 32

_FS_TABLE_COLUMNS: Final[tuple[str, ...]] = (
    "Archivo",
    "Estado",
    "Workflow",
    "Steps",
    "Errores / warnings",
)

_DB_TABLE_COLUMNS: Final[tuple[str, ...]] = (
    "ID",
    "Nombre",
    "Estado",
    "Steps",
    "Resumen",
)


class AutomationScreen(Screen[None]):
    """Listado de workflows del filesystem + historial de runs en DB."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "go_back", "Volver"),
        Binding("r", "refresh", "Refrescar"),
    ]

    def __init__(
        self,
        controller: WorkflowController,
        *,
        workflows_dir: str,
        check_credits: CreditsLoader,
        elevenlabs_client: ElevenLabsVoicesClient | None,
        audio_player: AudioPreviewPlayer | None,
        default_input_dir: Path,
        open_local_path: Callable[[Path], Awaitable[None]],
        default_i2v_duration_seconds: int,
        default_scene_approval_mode: SceneApprovalMode,
    ) -> None:
        super().__init__()
        self._controller = controller
        self._workflows_dir = workflows_dir
        self._check_credits = check_credits
        self._elevenlabs_client = elevenlabs_client
        self._audio_player = audio_player
        self._default_input_dir = default_input_dir
        self._open_local_path = open_local_path
        # Necesario para que el `WorkflowSummaryScreen` pueda mostrar la
        # duración efectiva del b-roll cuando NO hay override del modal
        # ni `step.duration_seconds` en el JSON. Sin esto la pantalla
        # hardcodearía un valor que puede diverger del .env.
        self._default_i2v_duration_seconds = default_i2v_duration_seconds
        self._default_scene_approval_mode = default_scene_approval_mode
        self._unsubscribe: Callable[[], None] | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="automation-box"):
            yield Static(
                "[b]Automatización — workflows JSON declarativos[/b]",
                id="automation-title",
            )
            yield Static(
                f"[dim]Directorio: {self._workflows_dir}/  ·  "
                "cada archivo .json = 1 workflow. La pantalla escanea al abrir y al "
                "presionar Refrescar (R).[/dim]",
                id="automation-subtitle",
            )
            yield Static("", id="automation-counters")
            yield Static("[b]Archivos detectados[/b]", classes="section-title")
            fs_table: DataTable[str] = DataTable(
                id="automation-fs-table", cursor_type="row", zebra_stripes=True
            )
            for column in _FS_TABLE_COLUMNS:
                fs_table.add_column(column, key=column)
            yield fs_table
            yield Static("[b]Historial de ejecuciones[/b]", classes="section-title")
            db_table: DataTable[str] = DataTable(
                id="automation-db-table", cursor_type="row", zebra_stripes=True
            )
            for column in _DB_TABLE_COLUMNS:
                db_table.add_column(column, key=column)
            yield db_table
            with Horizontal(classes="actions-row actions-row-keys"):
                yield Button("Configurar y ejecutar", id="automation-configure", variant="primary")
                yield Button("Ver detalle", id="automation-detail", classes="btn-info")
                yield Button("Reintentar", id="automation-retry", classes="btn-warning")
                yield Button(
                    "Revisar escena",
                    id="automation-approve",
                    classes="btn-success",
                )
                yield Button("Cancelar", id="automation-cancel", classes="btn-warning")
                yield Button("Refrescar", id="automation-refresh", classes="btn-info")
            yield Static(
                "[dim]Seleccioná un archivo de la tabla superior para configurarlo y "
                "ejecutarlo. Para ver el progreso paso a paso, seleccioná una ejecución "
                "en la tabla inferior y presioná 'Ver detalle'.[/dim]",
                id="automation-hint",
            )
            yield Static("", id="automation-status-bar")
        yield Footer()

    async def on_mount(self) -> None:
        await self._refresh_all(refresh_fs=True)
        self._unsubscribe = self._controller.subscribe(self._on_workflow_event)

    def on_unmount(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        handler = _BUTTON_HANDLERS.get(button_id)
        if handler is None:
            return
        await handler(self)

    def action_go_back(self) -> None:
        self.app.pop_screen()

    async def action_refresh(self) -> None:
        await self._refresh_all(refresh_fs=True)
        self._set_status("Listado refrescado")

    # --- event listener ---------------------------------------------------

    def _on_workflow_event(self, _event: WorkflowJobUpdated) -> None:
        """Refresca la tabla de DB cuando llega un evento. No hace FS rescan."""
        self.app.call_later(self._refresh_db_table)

    # --- handlers ---------------------------------------------------------

    async def _handle_refresh(self) -> None:
        await self.action_refresh()

    async def _handle_configure(self) -> None:
        """Inicia el flow Configurar → (Preview/Picker) → Summary → Enqueue.

        Usa el patrón **callback-based de Textual** (no `await push_screen`)
        para evitar cascadas de awaits que causan `InvalidStateError`
        cuando un modal se intenta cerrar mientras el siguiente está
        siendo manejado por el caller.
        """
        entry = await self._selected_fs_entry()
        if entry is None:
            return
        if not entry.valid:
            self._set_status(
                f"{ERROR} '{entry.name}' tiene errores: {'; '.join(entry.errors)}",
                error=True,
            )
            return
        self._start_enqueue_flow(entry)

    # --- enqueue flow (callback chain, NO awaits anidados) -------------

    def _start_enqueue_flow(self, entry: WorkflowEntry) -> None:
        """Abre `ConfigureWorkflowScreen` con callback para el siguiente paso."""

        def _on_configure_dismissed(result: ConfigureResult | None) -> None:
            if result is None:
                # Usuario canceló — nada más que hacer.
                return
            audio_language, i2v_duration_override, approval_mode, voice_changer = result
            pre_settings = _merge_pre_settings(
                entry,
                audio_language,
                i2v_duration_override,
                approval_mode,
                voice_changer,
                default_scene_approval_mode=self._default_scene_approval_mode,
            )
            self._dispatch_base_resolution(
                entry,
                audio_language=audio_language,
                pre_settings=pre_settings,
            )

        self.app.push_screen(
            ConfigureWorkflowScreen(
                entry=entry,
                default_i2v_duration_seconds=self._default_i2v_duration_seconds,
                default_scene_approval_mode=self._default_scene_approval_mode,
                elevenlabs_client=self._elevenlabs_client,
                audio_player=self._audio_player,
            ),
            _on_configure_dismissed,
        )

    def _dispatch_base_resolution(
        self,
        entry: WorkflowEntry,
        *,
        audio_language: str | None,
        pre_settings: WorkflowPreSettings,
    ) -> None:
        """Despacha al modal correcto según `model_creation.method`.

        - `prompt`:  PreviewBaseImageScreen genera + el usuario aprueba.
        - `local`:   ImageFilePickerScreen elige + subimos en background.
        - `catalog`: skip directo al summary (la ref se resuelve en runtime
          contra el store; el usuario ya eligió al editar el JSON).
        """
        method = pre_settings.model_creation.method
        if method == ModelCreationMethod.PROMPT:
            self._open_prompt_preview(
                entry,
                audio_language=audio_language,
                pre_settings=pre_settings,
            )
        elif method == ModelCreationMethod.LOCAL:
            self._open_local_picker(
                entry,
                audio_language=audio_language,
                pre_settings=pre_settings,
            )
        else:
            self._open_summary(
                entry,
                audio_language=audio_language,
                pre_settings=pre_settings,
                base_ref=None,
            )

    def _open_prompt_preview(
        self,
        entry: WorkflowEntry,
        *,
        audio_language: str | None,
        pre_settings: WorkflowPreSettings,
    ) -> None:
        prompt = pre_settings.model_creation.prompt or ""
        if not prompt:
            self._set_status(
                f"{ERROR} model_creation.method='prompt' requiere prompt no vacío",
                error=True,
            )
            return

        def _on_preview_dismissed(ref: ImageAssetRef | None) -> None:
            if ref is None:
                # Usuario canceló el preview — no abrimos el summary.
                return
            self._open_summary(
                entry,
                audio_language=audio_language,
                pre_settings=pre_settings,
                base_ref=ref,
            )

        initial_settings = None
        if pre_settings.image_aspect_ratio is not None:
            initial_settings = ImageGenerationSettings(aspect_ratio=pre_settings.image_aspect_ratio)

        self.app.push_screen(
            PreviewBaseImageScreen(
                controller=self._controller,
                prompt=prompt,
                label_hint=entry.name,
                open_local_path=self._open_local_path,
                initial_settings=initial_settings,
            ),
            _on_preview_dismissed,
        )

    def _open_local_picker(
        self,
        entry: WorkflowEntry,
        *,
        audio_language: str | None,
        pre_settings: WorkflowPreSettings,
    ) -> None:
        start_dir = self._default_input_dir
        local_path_hint = pre_settings.model_creation.local_path
        if local_path_hint:
            candidate = Path(local_path_hint)
            try:
                if candidate.is_file():
                    start_dir = candidate.parent
            except OSError:
                pass

        def _on_file_chosen(path: Path | None) -> None:
            if path is None:
                # Usuario canceló el picker.
                return
            self.app.run_worker(
                self._upload_and_open_summary(
                    entry,
                    audio_language=audio_language,
                    pre_settings=pre_settings,
                    local_path=path,
                ),
                exclusive=False,
            )

        self.app.push_screen(
            ImageFilePickerScreen(start_path=start_dir),
            _on_file_chosen,
        )

    async def _upload_and_open_summary(
        self,
        entry: WorkflowEntry,
        *,
        audio_language: str | None,
        pre_settings: WorkflowPreSettings,
        local_path: Path,
    ) -> None:
        self._set_status(f"Subiendo imagen base '{local_path.name}' a Kie…")
        try:
            base_ref = await self._controller.upload_local_base(local_path)
        except (ImageValidationError, WorkflowValidationError, KieError) as exc:
            self._set_status(f"{ERROR} no pude subir la imagen base: {exc}", error=True)
            return
        except Exception as exc:
            self._set_status(f"{ERROR} error inesperado subiendo: {exc}", error=True)
            return
        # Reflejamos el path local elegido en los pre_settings para que el
        # summary lo muestre. La ref de Kie viaja por `base_ref`.
        pre_settings.model_creation.local_path = str(local_path)
        self._set_status(f"{OK} imagen subida — revisá el resumen y confirmá")
        self._open_summary(
            entry,
            audio_language=audio_language,
            pre_settings=pre_settings,
            base_ref=base_ref,
        )

    def _open_summary(
        self,
        entry: WorkflowEntry,
        *,
        audio_language: str | None,
        pre_settings: WorkflowPreSettings,
        base_ref: ImageAssetRef | None,
    ) -> None:
        """Resuelve el producto (si aplica) y luego abre el summary.

        Punto de convergencia de los 3 caminos de resolución de base
        (prompt / local / catalog). Si el workflow promociona un producto
        y todavía no fue resuelto, abre un file picker para elegirlo desde
        `inputs/`, lo sube a Kie y lo guarda en `pre_settings.product_image`
        antes de continuar al summary.
        """
        needs_product = pre_settings.promote_product and not _product_already_resolved(pre_settings)
        if needs_product:
            self._open_product_picker(
                entry,
                audio_language=audio_language,
                pre_settings=pre_settings,
                base_ref=base_ref,
            )
            return
        self._open_summary_screen(
            entry,
            audio_language=audio_language,
            pre_settings=pre_settings,
            base_ref=base_ref,
        )

    def _open_product_picker(
        self,
        entry: WorkflowEntry,
        *,
        audio_language: str | None,
        pre_settings: WorkflowPreSettings,
        base_ref: ImageAssetRef | None,
    ) -> None:
        """Abre el file picker para elegir la imagen del producto promocional."""

        def _on_product_chosen(path: Path | None) -> None:
            if path is None:
                # Usuario canceló el picker del producto — abortamos el flujo
                # (no encolamos un workflow con promote_product sin producto).
                self._set_status(
                    f"{ERROR} promote_product=true requiere elegir un producto", error=True
                )
                return
            self.app.run_worker(
                self._upload_product_and_open_summary(
                    entry,
                    audio_language=audio_language,
                    pre_settings=pre_settings,
                    base_ref=base_ref,
                    product_path=path,
                ),
                exclusive=False,
            )

        self.app.push_screen(
            ImageFilePickerScreen(start_path=self._default_input_dir),
            _on_product_chosen,
        )

    def _retry_product_selection(
        self,
        entry: WorkflowEntry,
        *,
        audio_language: str | None,
        pre_settings: WorkflowPreSettings,
        base_ref: ImageAssetRef | None,
        message: str,
    ) -> None:
        self._set_status(
            f"{ERROR} {message}. Reintentá elegir el producto; "
            "la imagen base aprobada se conserva.",
            error=True,
        )
        self._open_product_picker(
            entry,
            audio_language=audio_language,
            pre_settings=pre_settings,
            base_ref=base_ref,
        )

    async def _upload_product_and_open_summary(
        self,
        entry: WorkflowEntry,
        *,
        audio_language: str | None,
        pre_settings: WorkflowPreSettings,
        base_ref: ImageAssetRef | None,
        product_path: Path,
    ) -> None:
        self._set_status(f"Subiendo producto '{product_path.name}' a Kie…")
        try:
            product_ref = await self._controller.upload_local_product(product_path)
        except (ImageValidationError, WorkflowValidationError, KieError) as exc:
            self._retry_product_selection(
                entry,
                audio_language=audio_language,
                pre_settings=pre_settings,
                base_ref=base_ref,
                message=f"no pude subir el producto: {exc}",
            )
            return
        except Exception as exc:
            self._retry_product_selection(
                entry,
                audio_language=audio_language,
                pre_settings=pre_settings,
                base_ref=base_ref,
                message=f"error inesperado subiendo el producto: {exc}",
            )
            return
        pre_settings.product_image = ProductImage(
            local_path=str(product_path), resolved_image_ref=product_ref
        )
        self._set_status(f"{OK} producto subido — revisá el resumen y confirmá")
        self._open_summary_screen(
            entry,
            audio_language=audio_language,
            pre_settings=pre_settings,
            base_ref=base_ref,
        )

    def _open_summary_screen(
        self,
        entry: WorkflowEntry,
        *,
        audio_language: str | None,
        pre_settings: WorkflowPreSettings,
        base_ref: ImageAssetRef | None,
    ) -> None:
        # Capturamos el local_path AHORA (puede mutar en pre_settings si el
        # usuario regresa al picker; queremos el snapshot del que aprobó).
        local_path = pre_settings.model_creation.local_path
        # El override de duración i2v ya vive en pre_settings (mutado por
        # `_merge_pre_settings`). Lo capturamos para pasarlo al controller
        # como kwarg explícito en vez de depender de que algún consumer
        # del controller lea el pre_settings (más explícito y testeable).
        i2v_duration_override = pre_settings.i2v_duration_seconds
        # Idem para scene_approval_mode (si el usuario lo cambió en el modal,
        # el override vive en pre_settings; lo pasamos explícito al controller).
        scene_approval_mode = pre_settings.scene_approval_mode
        # Producto resuelto (si el workflow lo promociona). Snapshot para
        # pasarlo explícito al controller en el enqueue.
        product = pre_settings.product_image
        product_ref = product.resolved_image_ref if product else None
        product_local_path = product.local_path if product else None
        voice_changer = (
            pre_settings.voice_changer.model_copy(deep=True)
            if pre_settings.voice_changer is not None
            else None
        )

        def _on_summary_dismissed(approved: bool | None) -> None:
            if not approved:
                # Usuario canceló o cerró sin confirmar.
                return
            self.app.run_worker(
                self._enqueue_after_summary(
                    entry,
                    audio_language=audio_language,
                    base_ref=base_ref,
                    local_path=local_path,
                    i2v_duration_override=i2v_duration_override,
                    scene_approval_mode=scene_approval_mode,
                    product_ref=product_ref,
                    product_local_path=product_local_path,
                    voice_changer=voice_changer,
                ),
                exclusive=False,
            )

        self.app.push_screen(
            WorkflowSummaryScreen(
                entry=entry,
                pre_settings=pre_settings,
                check_credits=self._check_credits,
            ),
            _on_summary_dismissed,
        )

    async def _enqueue_after_summary(
        self,
        entry: WorkflowEntry,
        *,
        audio_language: str | None,
        base_ref: ImageAssetRef | None,
        local_path: str | None,
        i2v_duration_override: int | None,
        scene_approval_mode: SceneApprovalMode,
        product_ref: ImageAssetRef | None,
        product_local_path: str | None,
        voice_changer: VoiceChangerSettings | None,
    ) -> None:
        try:
            workflow = await self._controller.enqueue_entry(
                entry,
                audio_language=audio_language,
                resolved_base_ref=base_ref,
                local_path=local_path,
                i2v_duration_override=i2v_duration_override,
                scene_approval_mode=scene_approval_mode,
                product_ref=product_ref,
                product_local_path=product_local_path,
                voice_changer=voice_changer,
                set_voice_changer=True,
            )
        except (WorkflowValidationError, WorkflowStepError, KieError) as exc:
            self._set_status(f"{ERROR} no pude encolar '{entry.name}': {exc}", error=True)
            return
        self._set_status(f"{OK} workflow '{workflow.name}' encolado (id={workflow.id[:14]}…)")
        await self._refresh_db_table()

    async def _handle_detail(self) -> None:
        workflow = await self._selected_db_workflow()
        if workflow is None:
            return
        await self.app.push_screen(
            WorkflowDetailScreen(controller=self._controller, workflow_id=workflow.id)
        )

    async def _handle_retry(self) -> None:
        workflow = await self._selected_db_workflow()
        if workflow is None:
            return
        try:
            product_ready = await self._controller.ensure_product_ready_for_retry(workflow.id)
        except (WorkflowNotFoundError, WorkflowValidationError, KieError) as exc:
            self._set_status(f"{ERROR} no pude preparar el retry: {exc}", error=True)
            await self._refresh_db_table()
            return
        if not product_ready:
            self._set_status(
                f"{ERROR} workflow '{workflow.name}' necesita recargar producto antes de reintentar",
                error=True,
            )
            self._open_retry_product_picker(workflow)
            return
        ok = await self._controller.retry(workflow.id)
        if ok:
            self._set_status(f"{OK} workflow '{workflow.name}' reencolado")
        else:
            self._set_status(
                f"{ERROR} workflow '{workflow.name}' no es reintentable (status={workflow.status.value})",
                error=True,
            )
        await self._refresh_db_table()

    def _open_retry_product_picker(self, workflow: WorkflowJob) -> None:
        """Abre picker de producto para un workflow ya creado (flujo de retry)."""
        start_dir = self._default_input_dir
        product = workflow.pre_settings.product_image
        if product and product.local_path:
            candidate = Path(product.local_path)
            try:
                if candidate.is_file():
                    start_dir = candidate.parent
            except OSError:
                pass

        def _on_product_chosen(path: Path | None) -> None:
            if path is None:
                self._set_status(f"{ERROR} retry cancelado: no se recargó el producto", error=True)
                return
            self.app.run_worker(
                self._replace_product_and_retry(workflow, path),
                exclusive=False,
            )

        self.app.push_screen(
            ImageFilePickerScreen(start_path=start_dir),
            _on_product_chosen,
        )

    async def _replace_product_and_retry(self, workflow: WorkflowJob, product_path: Path) -> None:
        self._set_status(
            f"Recargando producto '{product_path.name}' para workflow '{workflow.name}'…"
        )
        try:
            await self._controller.replace_workflow_product(workflow.id, product_path)
        except (
            ImageValidationError,
            WorkflowValidationError,
            KieError,
            WorkflowNotFoundError,
        ) as exc:
            self._set_status(f"{ERROR} no pude recargar el producto: {exc}", error=True)
            return
        ok = await self._controller.retry(workflow.id)
        if ok:
            self._set_status(f"{OK} workflow '{workflow.name}' reencolado con producto recargado")
        else:
            self._set_status(
                f"{ERROR} workflow '{workflow.name}' no es reintentable (status={workflow.status.value})",
                error=True,
            )
        await self._refresh_db_table()

    async def _handle_cancel(self) -> None:
        workflow = await self._selected_db_workflow()
        if workflow is None:
            return
        ok = await self._controller.cancel(workflow.id)
        if ok:
            self._set_status(f"{OK} workflow '{workflow.name}' cancelado")
        else:
            self._set_status(f"{ERROR} workflow '{workflow.name}' no es cancelable", error=True)
        await self._refresh_db_table()

    async def _handle_approve(self) -> None:
        """Abre el modal de aprobación si el workflow tiene un step esperando."""
        workflow = await self._selected_db_workflow()
        if workflow is None:
            return
        if not workflow.is_awaiting_approval():
            self._set_status(
                f"{ERROR} workflow '{workflow.name}' no está esperando aprobación "
                f"(status: {workflow.status.value})",
                error=True,
            )
            return
        step = workflow.pending_approval_step()
        if step is None:
            self._set_status(
                f"{ERROR} workflow '{workflow.name}' marcado AWAITING_APPROVAL "
                "pero ningún step en ese estado (inconsistencia; reintentá refresh)",
                error=True,
            )
            return

        def _on_dismissed(result: bool | None) -> None:
            if result:
                # Hubo acción → refrescar tabla para ver el nuevo estado.
                self.app.run_worker(self._refresh_db_table(), exclusive=False)

        self.app.push_screen(
            SceneImageApprovalScreen(
                controller=self._controller,
                workflow=workflow,
                step=step,
                open_local_path=self._open_local_path,
            ),
            _on_dismissed,
        )

    # --- table refresh ----------------------------------------------------

    async def _refresh_all(self, *, refresh_fs: bool) -> None:
        entries = await self._controller.list_entries(refresh=refresh_fs)
        workflows = await self._controller.list_workflows()
        self._refresh_fs_table(entries)
        self._refresh_db_table_with(workflows)
        self._update_counters(workflows)

    async def _refresh_db_table(self) -> None:
        workflows = await self._controller.list_workflows()
        self._refresh_db_table_with(workflows)
        self._update_counters(workflows)

    def _refresh_fs_table(self, entries: list[WorkflowEntry]) -> None:
        table = self.query_one("#automation-fs-table", DataTable)
        previous = get_selected_row_key(table)
        table.clear()
        for entry in entries:
            payload = entry.workflow_payload or {}
            wf_name = str(payload.get("workflow", entry.name))
            steps = len(payload.get("run", []) if isinstance(payload.get("run"), list) else [])
            if entry.valid:
                status_cell = f"[green]{OK} listo[/green]"
                detail_cell = format_warnings(entry.warnings)
            else:
                status_cell = f"[red]{ERROR} error[/red]"
                detail_cell = f"[red]{truncate('; '.join(entry.errors), 60)}[/red]"
            table.add_row(
                truncate(entry.name, _NAME_PREVIEW_LEN),
                status_cell,
                truncate(wf_name, _NAME_PREVIEW_LEN),
                str(steps),
                detail_cell,
                key=entry.name,
            )
        if previous is not None:
            select_row_by_key(table, previous)

    def _refresh_db_table_with(self, workflows: list[WorkflowJob]) -> None:
        table = self.query_one("#automation-db-table", DataTable)
        previous = get_selected_row_key(table)
        table.clear()
        for workflow in workflows:
            status_cell = format_workflow_status_cell(workflow.status)
            summary = build_workflow_run_summary(workflow)
            table.add_row(
                truncate(workflow.id, 22),
                truncate(workflow.name, _NAME_PREVIEW_LEN),
                status_cell,
                str(len(workflow.steps)),
                summary,
                key=workflow.id,
            )
        if previous is not None:
            select_row_by_key(table, previous)

    def _update_counters(self, workflows: list[WorkflowJob]) -> None:
        active = sum(
            1
            for w in workflows
            if w.status
            in {
                WorkflowStatus.PREPARING_BASE,
                WorkflowStatus.RUNNING,
            }
        )
        queued = sum(1 for w in workflows if w.status == WorkflowStatus.QUEUED)
        done = sum(1 for w in workflows if w.status == WorkflowStatus.COMPLETED)
        failed = sum(
            1
            for w in workflows
            if w.status in {WorkflowStatus.FAILED, WorkflowStatus.PARTIALLY_FAILED}
        )
        text = format_full_counters(
            len(workflows), active, queued, done, failed, active_label="activos"
        )
        self.query_one("#automation-counters", Static).update(text)

    # --- selection helpers ------------------------------------------------

    async def _selected_fs_entry(self) -> WorkflowEntry | None:
        table = self.query_one("#automation-fs-table", DataTable)
        key = get_selected_row_key(table)
        if key is None:
            self._set_status("Seleccioná un archivo en la tabla superior", error=True)
            return None
        entries = await self._controller.list_entries()
        for entry in entries:
            if entry.name == key:
                return entry
        self._set_status("Ese archivo ya no está en disco — refrescá", error=True)
        return None

    async def _selected_db_workflow(self) -> WorkflowJob | None:
        table = self.query_one("#automation-db-table", DataTable)
        key = get_selected_row_key(table)
        if key is None:
            self._set_status("Seleccioná una ejecución en la tabla inferior primero", error=True)
            return None
        workflow = await self._controller.get_workflow(key)
        if workflow is None:
            self._set_status("Esa ejecución ya no existe en la DB", error=True)
        return workflow

    def _set_status(self, message: str, *, error: bool = False) -> None:
        try:
            bar = self.query_one("#automation-status-bar", Static)
        except Exception:
            return
        bar.update(f"[red]{message}[/red]" if error else message)
        timeout = _LONG_NOTIFICATION_TIMEOUT if error else _NOTIFICATION_TIMEOUT
        self.notify(message, severity="error" if error else "information", timeout=timeout)


_BUTTON_HANDLERS: dict[str, Callable[[AutomationScreen], Awaitable[None]]] = {
    "automation-configure": AutomationScreen._handle_configure,
    "automation-detail": AutomationScreen._handle_detail,
    "automation-retry": AutomationScreen._handle_retry,
    "automation-cancel": AutomationScreen._handle_cancel,
    "automation-approve": AutomationScreen._handle_approve,
    "automation-refresh": AutomationScreen._handle_refresh,
}


def _product_already_resolved(pre_settings: WorkflowPreSettings) -> bool:
    """`True` si el producto ya fue subido a Kie (tiene ref resuelta).

    Evita re-abrir el file picker del producto si el flujo ya lo resolvió
    (ej. el usuario volvió atrás en la cadena de modales).
    """
    product = pre_settings.product_image
    return product is not None and product.resolved_image_ref is not None


def _merge_pre_settings(
    entry: WorkflowEntry,
    audio_language: str | None,
    i2v_duration_override: int | None,
    approval_mode: SceneApprovalMode | None,
    voice_changer: VoiceChangerSettings | None,
    *,
    default_scene_approval_mode: SceneApprovalMode,
) -> WorkflowPreSettings:
    """Parsea `pre_settings` del JSON y aplica overrides del modal Configurar.

    Pensada para construir el snapshot que muestra `WorkflowSummaryScreen`
    al usuario antes de encolar — debe reflejar EXACTO lo que el runner
    va a usar (incluyendo overrides).
    """
    payload = (entry.workflow_payload or {}).get("pre_settings", {})
    pre = WorkflowPreSettings.model_validate(payload)
    if isinstance(payload, dict) and "scene_approval_mode" not in payload:
        pre.scene_approval_mode = default_scene_approval_mode
    if audio_language is not None:
        pre.audio_language = audio_language
    if i2v_duration_override is not None:
        pre.i2v_duration_seconds = i2v_duration_override
    if approval_mode is not None:
        pre.scene_approval_mode = approval_mode
    pre.voice_changer = voice_changer.model_copy(deep=True) if voice_changer is not None else None
    return pre
