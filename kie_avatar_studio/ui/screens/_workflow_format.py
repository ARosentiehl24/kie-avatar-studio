"""Formatters compartidos entre `AutomationScreen` y `WorkflowDetailScreen`.

Extraído para CR-3.7 (sin duplicación) y CR-3.2 (bajar tamaño de las
pantallas que tenían formatters in-line).
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from ...domain.models import (
    WorkflowJob,
    WorkflowProgressKey,
    WorkflowStatus,
    WorkflowStep,
    WorkflowStepStatus,
)
from .._icons import ERROR, OK
from .._text_format import truncate

_STEP_STAGE_LABELS: Final[dict[WorkflowProgressKey, str]] = {
    WorkflowProgressKey.SCENE_IMAGE: "scene",
    WorkflowProgressKey.VIDEO: "veo",
    WorkflowProgressKey.DOWNLOAD: "video_local",
    WorkflowProgressKey.DOWNLOAD_VIDEO: "video_local",
}

_FINAL_OUTPUTS: Final[tuple[tuple[str, str], ...]] = (
    ("Video final", "final.mp4"),
    ("Audio final", "final_audio.mp3"),
    ("Audio con cambio de voz", "voice_changed_audio.mp3"),
)


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
    if status == WorkflowStatus.AWAITING_APPROVAL:
        # Resaltado especial: requiere acción humana.
        return f"[yellow]⏳ {status.value}[/yellow]"
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
        if key not in _STEP_STAGE_LABELS:
            continue
        label = _STEP_STAGE_LABELS[key]
        parts.append(f"{label}={_color_for_progress_status(status.value)}")
    if not parts:
        return "[dim]—[/dim]"
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
    video_label = f"{step.scene_slug}/video.mp4"
    if step.video_path:
        parts.append(video_label)
    elif step.status != WorkflowStepStatus.QUEUED:
        parts.append(f"[dim]{video_label}[/dim]")
    return ", ".join(parts) if parts else "[dim]—[/dim]"


def format_attached_status(step: WorkflowStep) -> str:
    """Indica si el step participa del concat final."""
    if step.attached:
        return "[green]✓[/green]"
    return "[dim]✗[/dim]"


def format_workflow_pipeline(workflow: WorkflowJob) -> str:
    """Resume el pipeline v2.0.0 del workflow con estados legibles."""
    parts = [
        _format_pipeline_stage("VEO 3.1", _veo_stage_status(workflow)),
        _format_pipeline_stage("Concatenación", _concat_stage_status(workflow)),
        _format_pipeline_stage("Extracción audio", _audio_stage_status(workflow)),
    ]
    if workflow.pre_settings.voice_changer is not None:
        parts.append(_format_pipeline_stage("Voice changer", _voice_changer_stage_status(workflow)))
    return "  ·  ".join(parts)


def format_workflow_outputs(workflow: WorkflowJob) -> str:
    """Lista los outputs finales esperados del workflow v2.0.0."""
    output_dir = Path(workflow.output_dir)
    lines = ["[b]Outputs v2.0.0[/b]"]
    for label, filename in _FINAL_OUTPUTS:
        if filename == "voice_changed_audio.mp3" and workflow.pre_settings.voice_changer is None:
            continue
        path = output_dir / filename
        status = "[green]✓ listo[/green]" if path.is_file() else "[yellow]pendiente[/yellow]"
        lines.append(f"  · {status} [b]{label}:[/b] [dim]{filename}[/dim]")
    return "\n".join(lines)


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


def _format_pipeline_stage(label: str, status: str) -> str:
    return f"[b]{label}:[/b] {_color_for_pipeline_status(status)}"


def _color_for_pipeline_status(status: str) -> str:
    if status == "done":
        return "[green]listo[/green]"
    if status == "running":
        return "[cyan]en curso[/cyan]"
    if status == "skipped":
        return "[dim]omitido[/dim]"
    if status == "failed":
        return "[red]falló[/red]"
    return "[yellow]pendiente[/yellow]"


def _veo_stage_status(workflow: WorkflowJob) -> str:
    if not workflow.steps:
        return "pending"
    if all(step.status == WorkflowStepStatus.COMPLETED for step in workflow.steps):
        return "done"
    if any(step.status == WorkflowStepStatus.FAILED for step in workflow.steps):
        return "failed"
    if any(
        step.status
        in {
            WorkflowStepStatus.PREPARING,
            WorkflowStepStatus.RENDERING,
            WorkflowStepStatus.DOWNLOADING,
            WorkflowStepStatus.AWAITING_APPROVAL,
        }
        for step in workflow.steps
    ):
        return "running"
    return "pending"


def _concat_stage_status(workflow: WorkflowJob) -> str:
    attached_steps = [step for step in workflow.steps if step.attached]
    if not attached_steps:
        return "skipped"
    if _workflow_output_exists(workflow, "final.mp4"):
        return "done"
    if workflow.status in {WorkflowStatus.FAILED, WorkflowStatus.PARTIALLY_FAILED}:
        return "failed"
    if all(step.status == WorkflowStepStatus.COMPLETED for step in attached_steps):
        return "running"
    return "pending"


def _audio_stage_status(workflow: WorkflowJob) -> str:
    attached_steps = [step for step in workflow.steps if step.attached]
    if not attached_steps:
        return "skipped"
    if _workflow_output_exists(workflow, "final_audio.mp3"):
        return "done"
    if workflow.status in {WorkflowStatus.FAILED, WorkflowStatus.PARTIALLY_FAILED}:
        return "failed"
    if _workflow_output_exists(workflow, "final.mp4"):
        return "running"
    return "pending"


def _voice_changer_stage_status(workflow: WorkflowJob) -> str:
    if _workflow_output_exists(workflow, "voice_changed_audio.mp3"):
        return "done"
    return "pending"


def _workflow_output_exists(workflow: WorkflowJob, filename: str) -> bool:
    return (Path(workflow.output_dir) / filename).is_file()


__all__ = [
    "build_workflow_run_summary",
    "format_attached_status",
    "format_outputs",
    "format_progress",
    "format_step_status",
    "format_warnings",
    "format_workflow_outputs",
    "format_workflow_pipeline",
    "format_workflow_status_cell",
    "format_workflow_status_label",
]
