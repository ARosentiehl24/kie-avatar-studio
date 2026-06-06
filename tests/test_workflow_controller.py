"""Tests del `WorkflowController` (casos de uso UI)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kie_avatar_studio.app_layer.queue_manager import QueueManager
from kie_avatar_studio.app_layer.workflow_controller import WorkflowController
from kie_avatar_studio.app_layer.workflow_lifecycle import WorkflowLifecycle
from kie_avatar_studio.config import Settings
from kie_avatar_studio.domain.errors import WorkflowValidationError
from kie_avatar_studio.domain.events import WorkflowJobUpdated
from kie_avatar_studio.domain.models import (
    VoicePreset,
    WorkflowJob,
)
from kie_avatar_studio.infra.generated_images_db import GeneratedImagesDB
from kie_avatar_studio.infra.images_db import ImagesDB
from kie_avatar_studio.infra.presets_store import VoicePresetsStore
from kie_avatar_studio.infra.workflow_db import WorkflowDB
from kie_avatar_studio.infra.workflow_loader import (
    build_workflow_from_entry,
    scan_workflows_dir,
)
from kie_avatar_studio.infra.workflow_manifest_writer import AtomicWorkflowManifestWriter


class _FakeRunner:
    """Runner que no ejecuta nada — solo registra invocaciones."""

    def __init__(self) -> None:
        self.runs: list[WorkflowJob] = []

    async def run(self, job: WorkflowJob) -> WorkflowJob:
        self.runs.append(job)
        return job


def _valid_payload(name: str = "Sample") -> dict:
    return {
        "workflow": name,
        "pre_settings": {
            "audio_language": "es-419",
            "voice_preset": "warm",
            "model_creation": {"method": "prompt", "prompt": "A woman"},
        },
        "run": [
            {
                "step": 1,
                "scene_name": "Hook",
                "type": "a-roll",
                "prompt": "Una persona habla a cámara",
                "text": "Hola mundo",
            }
        ],
    }


@pytest.fixture
async def workflow_controller_setup(
    tmp_settings: Settings,
) -> tuple[WorkflowController, _FakeRunner, Path, VoicePresetsStore]:
    workflows_dir = tmp_settings.workflows_dir
    workflows_dir.mkdir(parents=True, exist_ok=True)
    (workflows_dir / "valid.json").write_text(
        json.dumps(_valid_payload("Valid WF")), encoding="utf-8"
    )

    db = WorkflowDB(tmp_settings.db_path)
    await db.init()
    images_db = ImagesDB(tmp_settings.db_path)
    await images_db.init()
    generated_db = GeneratedImagesDB(tmp_settings.db_path)
    await generated_db.init()
    presets = VoicePresetsStore(tmp_settings.presets_dir)
    await presets.init()
    # Persistimos el preset "warm" para que la validación pase.
    await presets.upsert(
        VoicePreset(
            id="warm",
            label="Warm",
            voice_id="N2lVS1w4EtoT3dr4eOWO",
        )
    )
    fake_runner = _FakeRunner()
    lifecycle = WorkflowLifecycle(db)
    queue: QueueManager[WorkflowJob, WorkflowJobUpdated] = QueueManager(
        tmp_settings,
        fake_runner,
        event_factory=WorkflowJobUpdated,
        lifecycle=lifecycle,
    )
    controller = WorkflowController(
        tmp_settings,
        db,
        AtomicWorkflowManifestWriter(),
        queue,
        scan_loader=lambda: scan_workflows_dir(workflows_dir),
        entry_builder=build_workflow_from_entry,
        presets_store=presets,
        uploaded_images=images_db,
        generated_images=generated_db,
    )
    return controller, fake_runner, workflows_dir, presets


async def test_list_entries_caches_until_refresh(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path, VoicePresetsStore]
) -> None:
    controller, _, workflows_dir, _ = workflow_controller_setup
    entries = await controller.list_entries(refresh=True)
    assert len(entries) == 1
    # Agregamos un archivo nuevo sin refresh: la cache devuelve la misma lista.
    (workflows_dir / "new.json").write_text(json.dumps(_valid_payload("New")), encoding="utf-8")
    entries_cached = await controller.list_entries()
    assert len(entries_cached) == 1
    # Refresh detecta el nuevo.
    entries_fresh = await controller.list_entries(refresh=True)
    assert len(entries_fresh) == 2


async def test_enqueue_entry_persists_and_dispatches(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path, VoicePresetsStore]
) -> None:
    controller, fake_runner, _, _ = workflow_controller_setup
    entries = await controller.list_entries(refresh=True)
    workflow = await controller.enqueue_entry(entries[0])
    # Persistido en DB.
    loaded = await controller.get_workflow(workflow.id)
    assert loaded is not None
    assert loaded.name == "Valid WF"
    # El runner se invocó (FakeRunner.runs).
    assert any(j.id == workflow.id for j in fake_runner.runs)


async def test_enqueue_entry_rejects_invalid(
    tmp_settings: Settings,
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path, VoicePresetsStore],
) -> None:
    from kie_avatar_studio.domain.models import WorkflowEntry

    controller, _, _, _ = workflow_controller_setup
    bad_entry = WorkflowEntry(
        name="x", path=tmp_settings.workflows_dir / "x.json", errors=["fake"]
    )
    with pytest.raises(WorkflowValidationError, match="no es válido"):
        await controller.enqueue_entry(bad_entry)


async def test_enqueue_entry_overrides_voice_and_language(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path, VoicePresetsStore]
) -> None:
    controller, _, _, presets = workflow_controller_setup
    # Agregamos un preset alternativo para el override.
    await presets.upsert(
        VoicePreset(id="alt", label="Alt", voice_id="N2lVS1w4EtoT3dr4eOWO")
    )
    entries = await controller.list_entries(refresh=True)
    workflow = await controller.enqueue_entry(
        entries[0],
        voice_preset_id="alt",
        audio_language="pt-BR",
    )
    assert workflow.pre_settings.voice_preset_id == "alt"
    assert workflow.pre_settings.audio_language == "pt-BR"


async def test_enqueue_rejects_unknown_voice_preset(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path, VoicePresetsStore]
) -> None:
    controller, _, _, _ = workflow_controller_setup
    entries = await controller.list_entries(refresh=True)
    with pytest.raises(WorkflowValidationError, match="no existe en el catálogo"):
        await controller.enqueue_entry(entries[0], voice_preset_id="nope")


async def test_cancel_returns_false_for_unknown_id(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path, VoicePresetsStore]
) -> None:
    controller, _, _, _ = workflow_controller_setup
    ok = await controller.cancel("wf_does_not_exist")
    assert not ok


async def test_retry_returns_false_for_unknown_id(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path, VoicePresetsStore]
) -> None:
    controller, _, _, _ = workflow_controller_setup
    assert not await controller.retry("wf_does_not_exist")


async def test_list_workflows_returns_persisted_in_db(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path, VoicePresetsStore]
) -> None:
    controller, _, _, _ = workflow_controller_setup
    entries = await controller.list_entries(refresh=True)
    await controller.enqueue_entry(entries[0])
    workflows = await controller.list_workflows()
    assert len(workflows) == 1
    assert workflows[0].name == "Valid WF"
