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
