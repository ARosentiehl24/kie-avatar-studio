"""Widgets/formatos de la pantalla Configuración."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
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

from ...app_layer.settings_controller import EditableSettings
from ...domain.models import KieKey
from .._icons import ERROR, OK

KEY_TABLE_COLUMNS: tuple[str, ...] = (
    "Activa",
    "ID",
    "Label",
    "Key (masked)",
    "Última validación",
    "Saldo",
)
LOW_CREDITS_THRESHOLD = 5.0


def compose_settings_layout(snapshot: EditableSettings) -> ComposeResult:
    yield Header(show_clock=True)
    with Vertical(id="settings-box"):
        yield Static("[b]Configuración[/b]", id="settings-title")
        with TabbedContent(initial="tab-keys"):
            with TabPane("API Keys", id="tab-keys"):
                yield from compose_keys_tab()
            with TabPane("Endpoints", id="tab-endpoints"):
                yield from compose_endpoints_tab(snapshot)
            with TabPane("Ejecución", id="tab-execution"):
                yield from compose_execution_tab(snapshot)
            with TabPane("Concurrencia", id="tab-concurrency"):
                yield from compose_concurrency_tab(snapshot)
            with TabPane("Defaults", id="tab-defaults"):
                yield from compose_defaults_tab(snapshot)
            with TabPane("Mantenimiento", id="tab-maintenance"):
                yield from compose_maintenance_tab()
        yield Static("", id="status-bar")
    yield Footer()


def compose_keys_tab() -> ComposeResult:
    table: DataTable[str] = DataTable(id="keys-table", cursor_type="row", zebra_stripes=True)
    for column in KEY_TABLE_COLUMNS:
        table.add_column(column, key=column)
    yield table
    with Horizontal(classes="actions-row actions-row-keys"):
        yield Button("Agregar", id="key-add", variant="primary")
        yield Button("Activar", id="key-activate", classes="btn-info")
        yield Button("Probar", id="key-test", classes="btn-info")
        yield Button("Eliminar", id="key-delete", variant="error")


def compose_endpoints_tab(snapshot: EditableSettings) -> ComposeResult:
    with Vertical(classes="field-row"):
        yield Label("KIE_API_BASE")
        yield Input(value=snapshot.kie_api_base, id="kie-api-base")
        yield Label("KIE_UPLOAD_BASE")
        yield Input(value=snapshot.kie_upload_base, id="kie-upload-base")
    with Horizontal(classes="actions-row actions-row-save"):
        yield Button("Guardar endpoints", id="save-endpoints", variant="primary")


def compose_execution_tab(snapshot: EditableSettings) -> ComposeResult:
    with Vertical(classes="field-row"):
        yield Label("MAX_PARALLEL_JOBS (límite global compartido entre subsistemas)")
        yield Input(value=str(snapshot.max_parallel_jobs), id="max-parallel")
        yield Label("POLL_INTERVAL_SECONDS")
        yield Input(value=str(snapshot.poll_interval_seconds), id="poll-interval")
        yield Label("TASK_TIMEOUT_SECONDS")
        yield Input(value=str(snapshot.task_timeout_seconds), id="task-timeout")
    with Horizontal(classes="actions-row actions-row-save"):
        yield Button("Guardar ejecución", id="save-execution", variant="primary")


def compose_concurrency_tab(snapshot: EditableSettings) -> ComposeResult:
    yield Static(
        "Límites de paralelismo por subsistema (1-16). El [b]límite global[/b] "
        "(pestaña Ejecución) sigue siendo el techo absoluto compartido por todos. "
        "[yellow]Los cambios requieren reiniciar la app para aplicarse.[/yellow]"
    )
    with Vertical(classes="field-row"):
        yield Label("MAX_PARALLEL_AUDIO_JOBS (TTS ElevenLabs)")
        yield Input(value=str(snapshot.max_parallel_audio_jobs), id="max-parallel-audio")
        yield Label("MAX_PARALLEL_IMAGE_JOBS (Nano Banana / GPT Image)")
        yield Input(value=str(snapshot.max_parallel_image_jobs), id="max-parallel-image")
        yield Label("MAX_PARALLEL_VIDEO_JOBS (Avatar Pro / Kling 3.0)")
        yield Input(value=str(snapshot.max_parallel_video_jobs), id="max-parallel-video")
        yield Label("MAX_PARALLEL_UPLOAD_JOBS (Kie File Upload)")
        yield Input(value=str(snapshot.max_parallel_upload_jobs), id="max-parallel-upload")
        yield Label("MAX_PARALLEL_DOWNLOAD_JOBS (descargas a /outputs)")
        yield Input(value=str(snapshot.max_parallel_download_jobs), id="max-parallel-download")
    with Horizontal(classes="actions-row actions-row-save"):
        yield Button("Guardar concurrencia", id="save-concurrency", variant="primary")


def compose_defaults_tab(snapshot: EditableSettings) -> ComposeResult:
    with Vertical(classes="field-row"):
        yield Label("DEFAULT_VOICE")
        yield Input(value=snapshot.default_voice, id="default-voice")
        yield Label("DEFAULT_PROMPT")
        yield Input(value=snapshot.default_prompt, id="default-prompt")
    with Horizontal(classes="actions-row actions-row-save"):
        yield Button("Guardar defaults", id="save-defaults", variant="primary")


def compose_maintenance_tab() -> ComposeResult:
    yield Static(
        "Limpia únicamente la DB runtime (jobs, colas, historial y catálogos). "
        "Conserva API keys, outputs, inputs, presets y workflows."
    )
    with Horizontal(classes="actions-row actions-row-save"):
        yield Button("Limpiar DB runtime", id="cleanup-runtime-db", variant="error")


def format_validation_cell(key: KieKey) -> str:
    if key.last_validated_status is None or key.last_validated_at is None:
        return "—"
    when = key.last_validated_at.strftime("%Y-%m-%d %H:%M")
    glyph = {"ok": OK, "unauthorized": f"{ERROR} 401", "error": ERROR}.get(
        key.last_validated_status, "?"
    )
    return f"{glyph} {when}"


def format_credits_cell(key: KieKey) -> str:
    if key.last_known_credits is None:
        return "—"
    if key.last_known_credits <= LOW_CREDITS_THRESHOLD:
        return f"[red]{key.last_known_credits:.2f} cr[/red]"
    return f"{key.last_known_credits:.2f} cr"


def format_test_result(key: KieKey) -> str:
    status = key.last_validated_status
    if status == "ok":
        credits_suffix = (
            f" · saldo {key.last_known_credits:.2f} cr"
            if key.last_known_credits is not None
            else ""
        )
        return f"{OK} '{key.label}' validada contra Kie{credits_suffix}"
    if status == "unauthorized":
        return f"{ERROR} '{key.label}' rechazada por Kie (401/403)"
    return f"{ERROR} '{key.label}' no se pudo validar (error de red o servidor)"
