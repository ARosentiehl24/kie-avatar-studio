"""Tests del `WorkflowController` (casos de uso UI)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from kie_avatar_studio.app_layer.queue_manager import QueueManager
from kie_avatar_studio.app_layer.workflow_controller import WorkflowController
from kie_avatar_studio.app_layer.workflow_lifecycle import WorkflowLifecycle
from kie_avatar_studio.config import Settings
from kie_avatar_studio.domain.errors import WorkflowValidationError
from kie_avatar_studio.domain.events import WorkflowJobUpdated
from kie_avatar_studio.domain.models import (
    ImageAssetKind,
    ImageAssetRef,
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


class _FakeBaseResolver:
    """Base resolver fake configurable para tests de preview/upload."""

    def __init__(
        self,
        *,
        preview_ref: ImageAssetRef | None = None,
        upload_ref: ImageAssetRef | None = None,
    ) -> None:
        self.preview_ref = preview_ref
        self.upload_ref = upload_ref
        self.preview_calls: list[tuple[str, str, Path | None]] = []
        self.upload_calls: list[Path] = []

    async def generate_from_prompt_standalone(
        self,
        prompt: str,
        *,
        label_hint: str,
        download_to: Path | None = None,
        settings: object | None = None,
    ) -> ImageAssetRef:
        self.preview_calls.append((prompt, label_hint, download_to))
        if self.preview_ref is None:
            raise NotImplementedError("configurar `preview_ref` para tests que usan preview")
        if download_to is not None:
            download_to.parent.mkdir(parents=True, exist_ok=True)
            download_to.write_bytes(b"fake image bytes")
        return self.preview_ref

    async def upload_local_standalone(self, path: Path) -> ImageAssetRef:
        self.upload_calls.append(path)
        if self.upload_ref is None:
            raise NotImplementedError("configurar `upload_ref` para tests que usan upload")
        return self.upload_ref


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
        _FakeBaseResolver(),  # type: ignore[arg-type]
        scan_loader=lambda: scan_workflows_dir(workflows_dir),
        entry_builder=build_workflow_from_entry,
        presets_store=presets,
        uploaded_images=images_db,
        generated_images=generated_db,
    )
    return controller, fake_runner, workflows_dir, presets


async def test_list_entries_caches_until_refresh(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path, VoicePresetsStore],
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
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path, VoicePresetsStore],
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
    bad_entry = WorkflowEntry(name="x", path=tmp_settings.workflows_dir / "x.json", errors=["fake"])
    with pytest.raises(WorkflowValidationError, match="no es válido"):
        await controller.enqueue_entry(bad_entry)


async def test_enqueue_entry_overrides_voice_and_language(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path, VoicePresetsStore],
) -> None:
    controller, _, _, presets = workflow_controller_setup
    # Agregamos un preset alternativo para el override.
    await presets.upsert(VoicePreset(id="alt", label="Alt", voice_id="N2lVS1w4EtoT3dr4eOWO"))
    entries = await controller.list_entries(refresh=True)
    workflow = await controller.enqueue_entry(
        entries[0],
        voice_preset_id="alt",
        audio_language="pt-BR",
    )
    assert workflow.pre_settings.voice_preset_id == "alt"
    assert workflow.pre_settings.audio_language == "pt-BR"


async def test_enqueue_rejects_unknown_voice_preset(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path, VoicePresetsStore],
) -> None:
    controller, _, _, _ = workflow_controller_setup
    entries = await controller.list_entries(refresh=True)
    with pytest.raises(WorkflowValidationError, match="no existe en el catálogo"):
        await controller.enqueue_entry(entries[0], voice_preset_id="nope")


async def test_enqueue_resolves_preset_by_label(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path, VoicePresetsStore],
) -> None:
    """El JSON puede usar el label legible y el controller lo normaliza al id real."""
    controller, _, _, presets = workflow_controller_setup
    # Borramos "warm" y creamos uno con label distinto al id slug.
    await presets.delete("warm")
    await presets.upsert(
        VoicePreset(
            id="locutora_calmada",
            label="Locutora Calmada",
            voice_id="N2lVS1w4EtoT3dr4eOWO",
        )
    )
    entries = await controller.list_entries(refresh=True)
    # El JSON dice voice_preset = "Locutora Calmada" (label exacto).
    workflow = await controller.enqueue_entry(entries[0], voice_preset_id="Locutora Calmada")
    # El controller normaliza al id real, no al label.
    assert workflow.pre_settings.voice_preset_id == "locutora_calmada"


async def test_enqueue_resolves_preset_by_label_case_insensitive(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path, VoicePresetsStore],
) -> None:
    """El match por label es case-insensitive."""
    controller, _, _, presets = workflow_controller_setup
    await presets.delete("warm")
    await presets.upsert(
        VoicePreset(
            id="narrador",
            label="Narrador Documental",
            voice_id="N2lVS1w4EtoT3dr4eOWO",
        )
    )
    entries = await controller.list_entries(refresh=True)
    workflow = await controller.enqueue_entry(entries[0], voice_preset_id="narrador documental")
    assert workflow.pre_settings.voice_preset_id == "narrador"


async def test_enqueue_prefers_id_over_label_match(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path, VoicePresetsStore],
) -> None:
    """Si hay un id que matchea exacto, tiene prioridad sobre el label."""
    controller, _, _, presets = workflow_controller_setup
    await presets.delete("warm")
    # Creamos uno con id == "alpha" y otro con label == "alpha" (id distinto).
    await presets.upsert(
        VoicePreset(id="alpha", label="Alpha (canonical)", voice_id="N2lVS1w4EtoT3dr4eOWO")
    )
    await presets.upsert(VoicePreset(id="beta", label="alpha", voice_id="N2lVS1w4EtoT3dr4eOWO"))
    entries = await controller.list_entries(refresh=True)
    # Buscamos "alpha": debe matchear por id ("alpha"), no por label.
    workflow = await controller.enqueue_entry(entries[0], voice_preset_id="alpha")
    assert workflow.pre_settings.voice_preset_id == "alpha"


# --- pre-enqueue base resolution (preview + upload + resolved_base_ref) -


def _mock_generated_ref() -> ImageAssetRef:
    return ImageAssetRef(
        kind=ImageAssetKind.GENERATED,
        id="img_mock",
        label="mock generated",
        kie_url="https://tempfile.kie.ai/img/mock.png",
        expires_at=datetime.now(UTC) + timedelta(days=14),
    )


def _mock_uploaded_ref() -> ImageAssetRef:
    return ImageAssetRef(
        kind=ImageAssetKind.UPLOADED,
        id="uploads/local_mock.png",
        label="local_mock.png",
        kie_url="https://tempfile.kie.ai/uploads/local_mock.png",
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )


def _build_controller_with_fake_resolver(
    tmp_settings: Settings,
    db: WorkflowDB,
    images_db: ImagesDB,
    generated_db: GeneratedImagesDB,
    presets: VoicePresetsStore,
    workflows_dir: Path,
    fake_resolver: _FakeBaseResolver,
) -> tuple[WorkflowController, _FakeRunner]:
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
        fake_resolver,  # type: ignore[arg-type]
        scan_loader=lambda: scan_workflows_dir(workflows_dir),
        entry_builder=build_workflow_from_entry,
        presets_store=presets,
        uploaded_images=images_db,
        generated_images=generated_db,
    )
    return controller, fake_runner


async def test_preview_base_from_prompt_invokes_resolver_and_downloads(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path, VoicePresetsStore],
    tmp_settings: Settings,
) -> None:
    """`preview_base_from_prompt` debe llamar al resolver y producir el path."""
    _, _, workflows_dir, presets = workflow_controller_setup
    fake_resolver = _FakeBaseResolver(preview_ref=_mock_generated_ref())
    db = WorkflowDB(tmp_settings.db_path)
    await db.init()
    images_db = ImagesDB(tmp_settings.db_path)
    await images_db.init()
    generated_db = GeneratedImagesDB(tmp_settings.db_path)
    await generated_db.init()
    controller, _ = _build_controller_with_fake_resolver(
        tmp_settings, db, images_db, generated_db, presets, workflows_dir, fake_resolver
    )
    ref, path = await controller.preview_base_from_prompt(
        "A photorealistic woman", label_hint="test_wf"
    )
    assert ref.id == "img_mock"
    assert path.parent.name == "_previews"
    assert path.is_file()  # el fake resolver escribió bytes ahí
    assert fake_resolver.preview_calls == [("A photorealistic woman", "test_wf", path)]


async def test_upload_local_base_propagates_ref(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path, VoicePresetsStore],
    tmp_settings: Settings,
    tmp_path: Path,
) -> None:
    """`upload_local_base` debe propagar la ref devuelta por el resolver."""
    _, _, workflows_dir, presets = workflow_controller_setup
    fake_resolver = _FakeBaseResolver(upload_ref=_mock_uploaded_ref())
    db = WorkflowDB(tmp_settings.db_path)
    await db.init()
    images_db = ImagesDB(tmp_settings.db_path)
    await images_db.init()
    generated_db = GeneratedImagesDB(tmp_settings.db_path)
    await generated_db.init()
    controller, _ = _build_controller_with_fake_resolver(
        tmp_settings, db, images_db, generated_db, presets, workflows_dir, fake_resolver
    )
    fake_image = tmp_path / "fake.png"
    fake_image.write_bytes(b"fake")
    ref = await controller.upload_local_base(fake_image)
    assert ref.kind == ImageAssetKind.UPLOADED
    assert fake_resolver.upload_calls == [fake_image]


async def test_enqueue_with_resolved_base_ref_persists_and_skips_path_validation(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path, VoicePresetsStore],
) -> None:
    """`enqueue_entry(resolved_base_ref=...)` debe saltar validate_local_model_path."""
    controller, _, _, _ = workflow_controller_setup
    entries = await controller.list_entries(refresh=True)
    ref = _mock_uploaded_ref()
    # Pasamos ref + local_path; el path NO existe en disco pero igual el
    # encolado debe pasar (porque resolved_base_ref bypasea la validación).
    workflow = await controller.enqueue_entry(
        entries[0],
        resolved_base_ref=ref,
        local_path="/path/que/no/existe/modelo.png",
    )
    # Persistido en el workflow.
    persisted = workflow.pre_settings.model_creation.resolved_image_ref
    assert persisted is not None
    assert persisted.id == ref.id
    assert workflow.pre_settings.model_creation.local_path == "/path/que/no/existe/modelo.png"
    # El workflow ya está en la DB con la ref persistida.
    loaded = await controller.get_workflow(workflow.id)
    assert loaded is not None
    loaded_ref = loaded.pre_settings.model_creation.resolved_image_ref
    assert loaded_ref is not None
    assert loaded_ref.id == ref.id
    assert loaded.pre_settings.model_creation.local_path == "/path/que/no/existe/modelo.png"


async def test_enqueue_without_resolved_base_ref_still_validates_path(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path, VoicePresetsStore],
) -> None:
    """Sin `resolved_base_ref`, el validator del path se ejecuta normalmente."""
    from kie_avatar_studio.domain.errors import ImageValidationError

    controller, _, workflows_dir, _ = workflow_controller_setup
    # Sobreescribimos el JSON con method=local + local_path inexistente.
    bad_payload = {
        "workflow": "Bad Local",
        "pre_settings": {
            "voice_preset": "warm",
            "model_creation": {"method": "local", "local_path": "/nope/no.png"},
        },
        "run": [
            {
                "step": 1,
                "scene_name": "Hook",
                "type": "a-roll",
                "prompt": "p",
                "text": "t",
            }
        ],
    }
    (workflows_dir / "bad_local.json").write_text(json.dumps(bad_payload), encoding="utf-8")
    entries = await controller.list_entries(refresh=True)
    bad_entry = next(e for e in entries if e.name == "bad_local")
    with pytest.raises(ImageValidationError):
        await controller.enqueue_entry(bad_entry)


async def test_cancel_returns_false_for_unknown_id(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path, VoicePresetsStore],
) -> None:
    controller, _, _, _ = workflow_controller_setup
    ok = await controller.cancel("wf_does_not_exist")
    assert not ok


async def test_retry_returns_false_for_unknown_id(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path, VoicePresetsStore],
) -> None:
    controller, _, _, _ = workflow_controller_setup
    assert not await controller.retry("wf_does_not_exist")


async def test_list_workflows_returns_persisted_in_db(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path, VoicePresetsStore],
) -> None:
    controller, _, _, _ = workflow_controller_setup
    entries = await controller.list_entries(refresh=True)
    await controller.enqueue_entry(entries[0])
    workflows = await controller.list_workflows()
    assert len(workflows) == 1
    assert workflows[0].name == "Valid WF"


# --- approval flow (SceneApprovalMode.MANUAL) -----------------------------


async def _enqueue_awaiting_workflow(
    controller: WorkflowController, entries: list, step_number: int = 1
):
    """Helper: encola un workflow y pone un step en AWAITING_APPROVAL para tests."""
    from kie_avatar_studio.domain.models import (
        SceneApprovalMode,
        WorkflowStatus,
        WorkflowStepStatus,
    )

    workflow = await controller.enqueue_entry(
        entries[0], scene_approval_mode=SceneApprovalMode.MANUAL
    )
    # Simulamos lo que haría el runner: pausa el step y el workflow.
    target = workflow.step_by_number(step_number)
    assert target is not None
    target.status = WorkflowStepStatus.AWAITING_APPROVAL
    target.bg_image_job_id = f"img_test_{step_number}"
    target.scene_image_path = f"/tmp/scene_{step_number}.png"
    workflow.status = WorkflowStatus.AWAITING_APPROVAL
    # Persistimos vía la API pública del controller (passthrough al repo).
    await controller._repository.update_workflow_header(workflow)  # type: ignore[attr-defined]
    await controller._repository.upsert_step(workflow.id, target)  # type: ignore[attr-defined]
    return workflow


async def test_approve_scene_marks_approved_and_requeues(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path, VoicePresetsStore],
) -> None:
    from kie_avatar_studio.domain.models import WorkflowStatus, WorkflowStepStatus

    controller, _fake_runner, _, _ = workflow_controller_setup
    entries = await controller.list_entries(refresh=True)
    workflow = await _enqueue_awaiting_workflow(controller, entries)
    result = await controller.approve_scene(workflow.id, 1)
    step = result.step_by_number(1)
    assert step is not None
    assert step.scene_image_approved_at is not None
    assert step.status == WorkflowStepStatus.QUEUED
    assert result.status == WorkflowStatus.QUEUED


async def test_regenerate_scene_resets_and_requeues(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path, VoicePresetsStore],
) -> None:
    from kie_avatar_studio.domain.models import WorkflowStatus, WorkflowStepStatus

    controller, _fake_runner, _, _ = workflow_controller_setup
    entries = await controller.list_entries(refresh=True)
    workflow = await _enqueue_awaiting_workflow(controller, entries)
    result = await controller.regenerate_scene(workflow.id, 1)
    step = result.step_by_number(1)
    assert step is not None
    assert step.bg_image_job_id is None
    assert step.scene_image_path is None
    assert step.scene_image_approved_at is None
    assert step.status == WorkflowStepStatus.QUEUED
    assert result.status == WorkflowStatus.QUEUED


async def test_cancel_step_marks_cancelled_and_continues_workflow(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path, VoicePresetsStore],
) -> None:
    from kie_avatar_studio.domain.models import WorkflowStatus, WorkflowStepStatus

    controller, _, _, _ = workflow_controller_setup
    entries = await controller.list_entries(refresh=True)
    workflow = await _enqueue_awaiting_workflow(controller, entries)
    result = await controller.cancel_step(workflow.id, 1)
    step = result.step_by_number(1)
    assert step is not None
    assert step.status == WorkflowStepStatus.CANCELLED
    assert step.completed_at is not None
    # El workflow vuelve a QUEUED para que el runner continúe con otros steps.
    assert result.status == WorkflowStatus.QUEUED


async def test_approve_scene_rejects_step_not_awaiting(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path, VoicePresetsStore],
) -> None:
    from kie_avatar_studio.domain.errors import WorkflowValidationError

    controller, _, _, _ = workflow_controller_setup
    entries = await controller.list_entries(refresh=True)
    workflow = await controller.enqueue_entry(entries[0])
    # Step queda en QUEUED (no AWAITING_APPROVAL). Aprobar debe fallar.
    with pytest.raises(WorkflowValidationError, match="no está esperando aprobación"):
        await controller.approve_scene(workflow.id, 1)


async def test_approve_scene_rejects_unknown_workflow(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path, VoicePresetsStore],
) -> None:
    from kie_avatar_studio.domain.errors import WorkflowNotFoundError

    controller, _, _, _ = workflow_controller_setup
    with pytest.raises(WorkflowNotFoundError):
        await controller.approve_scene("wf_does_not_exist", 1)
