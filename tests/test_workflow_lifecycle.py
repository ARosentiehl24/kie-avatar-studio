"""Tests del `WorkflowLifecycle` (cancel/retry rules)."""

from __future__ import annotations

import pytest

from kie_avatar_studio.app_layer.workflow_lifecycle import WorkflowLifecycle
from kie_avatar_studio.config import Settings
from kie_avatar_studio.domain.models import (
    ModelCreation,
    ModelCreationMethod,
    StepType,
    WorkflowJob,
    WorkflowPreSettings,
    WorkflowStatus,
    WorkflowStep,
)
from kie_avatar_studio.infra.workflow_db import WorkflowDB


def _make_workflow(status: WorkflowStatus = WorkflowStatus.QUEUED) -> WorkflowJob:
    return WorkflowJob(
        id="wf_test_001",
        name="Test",
        slug="test",
        source_json_path="workflows/test.json",
        output_dir="outputs/wf_test_001",
        pre_settings=WorkflowPreSettings(
            model_creation=ModelCreation(method=ModelCreationMethod.PROMPT, prompt="A woman"),
        ),
        steps=[
            WorkflowStep(
                step=1,
                scene_name="Hook",
                scene_slug="hook",
                type=StepType.A_ROLL,
                prompt="prompt",
                text="hola",
            )
        ],
        status=status,
    )


@pytest.fixture
async def lifecycle(tmp_settings: Settings) -> WorkflowLifecycle:
    db = WorkflowDB(tmp_settings.db_path)
    await db.init()
    return WorkflowLifecycle(db)


class TestIsCancellable:
    def test_queued_is_cancellable(self, lifecycle: WorkflowLifecycle) -> None:
        assert lifecycle.is_cancellable(_make_workflow(WorkflowStatus.QUEUED))

    def test_running_is_cancellable(self, lifecycle: WorkflowLifecycle) -> None:
        assert lifecycle.is_cancellable(_make_workflow(WorkflowStatus.RUNNING))

    def test_preparing_base_is_cancellable(self, lifecycle: WorkflowLifecycle) -> None:
        assert lifecycle.is_cancellable(_make_workflow(WorkflowStatus.PREPARING_BASE))

    def test_completed_is_not_cancellable(self, lifecycle: WorkflowLifecycle) -> None:
        assert not lifecycle.is_cancellable(_make_workflow(WorkflowStatus.COMPLETED))

    def test_failed_is_not_cancellable(self, lifecycle: WorkflowLifecycle) -> None:
        assert not lifecycle.is_cancellable(_make_workflow(WorkflowStatus.FAILED))

    def test_cancelled_is_not_cancellable(self, lifecycle: WorkflowLifecycle) -> None:
        assert not lifecycle.is_cancellable(_make_workflow(WorkflowStatus.CANCELLED))

    def test_partially_failed_is_not_cancellable(self, lifecycle: WorkflowLifecycle) -> None:
        assert not lifecycle.is_cancellable(_make_workflow(WorkflowStatus.PARTIALLY_FAILED))


class TestIsRetryable:
    def test_failed_is_retryable(self, lifecycle: WorkflowLifecycle) -> None:
        assert lifecycle.is_retryable(_make_workflow(WorkflowStatus.FAILED))

    def test_cancelled_is_retryable(self, lifecycle: WorkflowLifecycle) -> None:
        assert lifecycle.is_retryable(_make_workflow(WorkflowStatus.CANCELLED))

    def test_partially_failed_is_retryable(self, lifecycle: WorkflowLifecycle) -> None:
        assert lifecycle.is_retryable(_make_workflow(WorkflowStatus.PARTIALLY_FAILED))

    def test_completed_is_not_retryable(self, lifecycle: WorkflowLifecycle) -> None:
        assert not lifecycle.is_retryable(_make_workflow(WorkflowStatus.COMPLETED))

    def test_queued_is_not_retryable(self, lifecycle: WorkflowLifecycle) -> None:
        assert not lifecycle.is_retryable(_make_workflow(WorkflowStatus.QUEUED))


class TestMarkCancelled:
    async def test_persists_cancelled_status(
        self, lifecycle: WorkflowLifecycle, tmp_settings: Settings
    ) -> None:
        db = WorkflowDB(tmp_settings.db_path)
        workflow = _make_workflow(WorkflowStatus.RUNNING)
        await db.upsert_workflow(workflow)
        await lifecycle.mark_cancelled(workflow)
        loaded = await db.get(workflow.id)
        assert loaded is not None
        assert loaded.status == WorkflowStatus.CANCELLED


class TestResetForRetry:
    async def test_resets_header_keeping_steps(
        self, lifecycle: WorkflowLifecycle, tmp_settings: Settings
    ) -> None:
        db = WorkflowDB(tmp_settings.db_path)
        workflow = _make_workflow(WorkflowStatus.FAILED)
        workflow.error = "some error"
        workflow.manifest_write_failed = True
        await db.upsert_workflow(workflow)
        await lifecycle.reset_for_retry(workflow)
        loaded = await db.get(workflow.id)
        assert loaded is not None
        assert loaded.status == WorkflowStatus.QUEUED
        assert loaded.error is None
        assert not loaded.manifest_write_failed
        assert len(loaded.steps) == 1  # NO se borraron los steps

    async def test_resets_failed_and_cancelled_steps_to_queued(
        self, lifecycle: WorkflowLifecycle, tmp_settings: Settings
    ) -> None:
        """`reset_for_retry` debe resetear todos los steps en FAILED o CANCELLED
        de vuelta a QUEUED y limpiar su progress e IDs para que se puedan re-ejecutar."""
        from kie_avatar_studio.domain.models import (
            WorkflowProgressKey,
            WorkflowProgressStatus,
            WorkflowStepStatus,
        )

        db = WorkflowDB(tmp_settings.db_path)
        workflow = _make_workflow(WorkflowStatus.FAILED)
        # Añadimos un step fallido, uno cancelado y uno completado (para verificar
        # que el completado NO se toca).
        from copy import deepcopy

        s_fail = deepcopy(workflow.steps[0])
        s_fail.step = 1
        s_fail.status = WorkflowStepStatus.FAILED
        s_fail.error = "failed audio"
        s_fail.audio_job_id = "aud_123"
        s_fail.progress = {WorkflowProgressKey.AUDIO: WorkflowProgressStatus.FAILED}

        s_cancel = deepcopy(workflow.steps[0])
        s_cancel.step = 2
        s_cancel.status = WorkflowStepStatus.CANCELLED
        s_cancel.error = "cancelado"
        s_cancel.video_task_id = "vid_123"
        s_cancel.progress = {WorkflowProgressKey.VIDEO: WorkflowProgressStatus.FAILED}

        s_ok = deepcopy(workflow.steps[0])
        s_ok.step = 3
        s_ok.status = WorkflowStepStatus.COMPLETED
        s_ok.video_task_id = "vid_ok_123"

        workflow.steps = [s_fail, s_cancel, s_ok]
        await db.upsert_workflow(workflow)

        # Resetear
        await lifecycle.reset_for_retry(workflow)

        loaded = await db.get(workflow.id)
        assert loaded is not None
        assert loaded.status == WorkflowStatus.QUEUED

        # Step 1 (FAILED) debe estar en QUEUED y limpio
        st1 = loaded.steps[0]
        assert st1.status == WorkflowStepStatus.QUEUED
        assert st1.error is None
        assert st1.audio_job_id is None
        assert not st1.progress

        # Step 2 (CANCELLED) debe estar en QUEUED y limpio
        st2 = loaded.steps[1]
        assert st2.status == WorkflowStepStatus.QUEUED
        assert st2.error is None
        assert st2.video_task_id is None
        assert not st2.progress

        # Step 3 (COMPLETED) NO debe haberse modificado
        st3 = loaded.steps[2]
        assert st3.status == WorkflowStepStatus.COMPLETED
        assert st3.video_task_id == "vid_ok_123"
