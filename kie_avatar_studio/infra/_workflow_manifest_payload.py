from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any

from ..domain.models import WorkflowJob, WorkflowStep, WorkflowStepStatus
from ..domain.workflow_artifacts import (
    workflow_base_image_candidates,
    workflow_final_audio_candidates,
    workflow_final_video_candidates,
    workflow_voice_changed_audio_candidates,
)

ManifestJson = dict[str, Any]  # Any: payload JSON heterogéneo publicado en workflow.json.


async def build_manifest_payload(workflow: WorkflowJob) -> ManifestJson:
    return {
        "id": workflow.id,
        "name": workflow.name,
        "slug": workflow.slug,
        "status": workflow.status.value,
        "progress_summary": _summarize_steps(workflow.steps),
        "created_at": workflow.created_at.isoformat(),
        "updated_at": workflow.updated_at.isoformat(),
        "source_json_path": workflow.source_json_path,
        "output_dir": workflow.output_dir,
        "error": workflow.error,
        "manifest_write_failed": workflow.manifest_write_failed,
        "pre_settings": workflow.pre_settings.model_dump(by_alias=True, mode="json"),
        "model_base": await _model_base_block(workflow),
        "product": _product_block(workflow),
        "outputs": await _workflow_outputs_block(workflow),
        "steps": [_step_block(step) for step in workflow.steps],
    }


def _product_block(workflow: WorkflowJob) -> ManifestJson | None:
    pre = workflow.pre_settings
    if not pre.promote_product or pre.product_image is None:
        return None
    product = pre.product_image
    ref = product.resolved_image_ref
    block: ManifestJson = {"local_path": product.local_path}
    if ref is not None:
        block.update(
            {
                "kind": ref.kind.value,
                "id": ref.id,
                "label": ref.label,
                "kie_url": ref.kie_url,
                "expires_at": ref.expires_at.isoformat(),
            }
        )
    return block


async def _model_base_block(workflow: WorkflowJob) -> ManifestJson | None:
    ref = workflow.pre_settings.model_creation.resolved_image_ref
    if ref is None:
        return None
    return {
        "kind": ref.kind.value,
        "id": ref.id,
        "label": ref.label,
        "kie_url": ref.kie_url,
        "expires_at": ref.expires_at.isoformat(),
        "local_path": await _base_local_path(workflow),
    }


async def _base_local_path(workflow: WorkflowJob) -> str | None:
    for base in workflow_base_image_candidates(workflow):
        exists = await asyncio.to_thread(base.exists)
        if exists:
            return str(base)
    return None


def _step_block(step: WorkflowStep) -> ManifestJson:
    return {
        "step": step.step,
        "scene_name": step.scene_name,
        "scene_slug": step.scene_slug,
        "type": step.type.value,
        "change_scene": step.change_scene,
        "scene_description": step.scene_description,
        "prompt": step.prompt,
        "text": step.text,
        "duration_seconds": step.duration_seconds,
        "voiceover": step.voiceover,
        "include_product": step.include_product,
        "include_model": step.include_model,
        "set_as_base": step.set_as_base,
        "product_prompt": step.product_prompt,
        "image_aspect_ratio": step.image_aspect_ratio,
        "scene_image_approved_at": (
            step.scene_image_approved_at.isoformat() if step.scene_image_approved_at else None
        ),
        "status": step.status.value,
        "progress": {key.value: value.value for key, value in step.progress.items()},
        "outputs": _step_outputs(step),
        "error": step.error,
        "started_at": step.started_at.isoformat() if step.started_at else None,
        "completed_at": step.completed_at.isoformat() if step.completed_at else None,
    }


def _step_outputs(step: WorkflowStep) -> dict[str, str]:
    candidates = {
        "scene_image": step.scene_image_path,
        "audio": step.audio_path,
        "video": step.video_path,
    }
    return {key: value for key, value in candidates.items() if value}


async def _workflow_outputs_block(workflow: WorkflowJob) -> dict[str, str]:
    candidates = {
        "video": workflow_final_video_candidates(workflow),
        "audio": workflow_final_audio_candidates(workflow),
        "voice_changed_audio": workflow_voice_changed_audio_candidates(workflow),
    }
    existing: dict[str, str] = {}
    for key, paths in candidates.items():
        for path in paths:
            if await asyncio.to_thread(path.is_file):
                existing[key] = str(path)
                break
    return existing


def _summarize_steps(steps: list[WorkflowStep]) -> str:
    counts = Counter(step.status for step in steps)
    parts: list[str] = []
    for status, label in (
        (WorkflowStepStatus.COMPLETED, "completados"),
        (WorkflowStepStatus.FAILED, "fallidos"),
        (WorkflowStepStatus.CANCELLED, "cancelados"),
    ):
        if counts[status]:
            parts.append(f"{counts[status]} {label}")
    running = sum(
        counts[status]
        for status in (
            WorkflowStepStatus.PREPARING,
            WorkflowStepStatus.RENDERING,
            WorkflowStepStatus.DOWNLOADING,
        )
    )
    if running:
        parts.append(f"{running} en curso")
    if counts[WorkflowStepStatus.QUEUED]:
        parts.append(f"{counts[WorkflowStepStatus.QUEUED]} pendientes")
    if not parts:
        return f"sin progreso aún (0 de {len(steps)})"
    return ", ".join(parts) + f" de {len(steps)}"
