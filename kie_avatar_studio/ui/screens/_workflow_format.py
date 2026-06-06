"""Formatters compartidos entre `AutomationScreen` y `WorkflowDetailScreen`.

Extraído para CR-3.7 (sin duplicación) y CR-3.2 (bajar tamaño de las
pantallas que tenían formatters in-line).
"""

from __future__ import annotations

from ...domain.models import (
    WorkflowJob,
    WorkflowStatus,
    WorkflowStep,
    WorkflowStepStatus,
)
from .._icons import ERROR, OK
from .._text_format import truncate


def format_warnings(warnings: list[str]) -> str:
    """Renderiza la lista de warnings como una sola celda, con color amarillo."""
    if not warnings:
        return "—"
    preview = truncate("; ".join(warnings), 60)
    return f"[yellow]{preview}[/yellow]"


def format_workflow_status_cell(status: WorkflowStatus) -> str:
    """Render del status del workflow con color/icono semánticos."""
    if status == WorkflowStatus.COMPLETED:
        return f"[green]{OK} {status.value}[/green]"
    if status in {WorkflowStatus.FAILED, WorkflowStatus.PARTIALLY_FAILED}:
        return f"[red]{ERROR} {status.value}[/red]"
    if status == WorkflowStatus.CANCELLED:
        return f"[dim]{status.value}[/dim]"
    if status in {WorkflowStatus.RUNNING, WorkflowStatus.PREPARING_BASE}:
        return f"[cyan]{status.value}[/cyan]"
    return f"[yellow]{status.value}[/yellow]"


def format_workflow_status_label(workflow: WorkflowJob) -> str:
    """Render del status para el header de detalle (con error inline si hay)."""
    status = workflow.status.value
    if workflow.error:
        return f"[red]{status}[/red] — {truncate(workflow.error, 60)}"
    return f"[cyan]{status}[/cyan]"


def format_step_status(step: WorkflowStep) -> str:
    status = step.status.value
    if step.status == WorkflowStepStatus.COMPLETED:
        return f"[green]{OK} {status}[/green]"
    if step.status == WorkflowStepStatus.FAILED:
        return f"[red]{ERROR} {status}[/red]"
    if step.status == WorkflowStepStatus.CANCELLED:
        return f"[dim]{status}[/dim]"
    if step.status in {
        WorkflowStepStatus.PREPARING,
        WorkflowStepStatus.RENDERING,
        WorkflowStepStatus.DOWNLOADING,
    }:
        return f"[cyan]{status}[/cyan]"
    return f"[yellow]{status}[/yellow]"


def format_progress(step: WorkflowStep) -> str:
    if not step.progress:
        return "[dim]—[/dim]"
    parts: list[str] = []
    for key, status in sorted(step.progress.items(), key=lambda kv: kv[0].value):
        parts.append(f"{key.value}={_color_for_progress_status(status.value)}")
    return " · ".join(parts)


def _color_for_progress_status(value: str) -> str:
    if value == "completed":
        return f"[green]{value}[/green]"
    if value == "running":
        return f"[cyan]{value}[/cyan]"
    if value == "failed":
        return f"[red]{value}[/red]"
    if value == "skipped":
        return f"[dim]{value}[/dim]"
    return f"[yellow]{value}[/yellow]"


def format_outputs(step: WorkflowStep) -> str:
    parts: list[str] = []
    if step.scene_image_path:
        parts.append("scene.png")
    if step.audio_path:
        parts.append("audio.mp3")
    if step.video_path:
        parts.append(step.video_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1])
    return ", ".join(parts) if parts else "[dim]—[/dim]"


def build_workflow_run_summary(workflow: WorkflowJob) -> str:
    """Resumen corto para la celda 'Resumen' de la tabla de runs."""
    if workflow.error:
        return f"[red]{truncate(workflow.error, 60)}[/red]"
    completed = sum(1 for s in workflow.steps if s.status == WorkflowStepStatus.COMPLETED)
    failed = sum(1 for s in workflow.steps if s.status == WorkflowStepStatus.FAILED)
    parts: list[str] = []
    if completed:
        parts.append(f"{completed} ok")
    if failed:
        parts.append(f"{failed} fail")
    if not parts:
        return "—"
    return " · ".join(parts)


__all__ = [
    "build_workflow_run_summary",
    "format_outputs",
    "format_progress",
    "format_step_status",
    "format_warnings",
    "format_workflow_status_cell",
    "format_workflow_status_label",
]
