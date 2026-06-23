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
    VoiceChangerSettings,
    VoiceSettings,
    WorkflowJob,
    WorkflowProgressKey,
    WorkflowProgressStatus,
    WorkflowStatus,
    WorkflowStepStatus,
)
from kie_avatar_studio.infra.generated_images_db import GeneratedImagesDB
from kie_avatar_studio.infra.images_db import ImagesDB
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


def _valid_product_payload(name: str = "Sample Product WF") -> dict:
    return {
        "workflow": name,
        "pre_settings": {
            "audio_language": "es-419",
            "promote_product": True,
            "model_creation": {"method": "prompt", "prompt": "A woman"},
        },
        "run": [
            {
                "step": 1,
                "scene_name": "Hook con producto",
                "type": "a-roll",
                "prompt": "Una persona muestra un producto",
                "text": "Acá tenés el producto",
                "include_product": True,
                "include_model": True,
            }
        ],
    }


@pytest.fixture
async def workflow_controller_setup(
    tmp_settings: Settings,
) -> tuple[WorkflowController, _FakeRunner, Path]:
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
        AtomicWorkflowManifestWriter(tmp_settings.outputs_dir),
        queue,
        _FakeBaseResolver(),  # type: ignore[arg-type]
        scan_loader=lambda: scan_workflows_dir(workflows_dir),
        entry_builder=build_workflow_from_entry,
        uploaded_images=images_db,
        generated_images=generated_db,
    )
    return controller, fake_runner, workflows_dir


async def test_list_entries_caches_until_refresh(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path],
) -> None:
    controller, _, workflows_dir = workflow_controller_setup
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
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path],
) -> None:
    controller, fake_runner, _ = workflow_controller_setup
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
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path],
) -> None:
    from kie_avatar_studio.domain.models import WorkflowEntry

    controller, _, _ = workflow_controller_setup
    bad_entry = WorkflowEntry(name="x", path=tmp_settings.workflows_dir / "x.json", errors=["fake"])
    with pytest.raises(WorkflowValidationError, match="no es válido"):
        await controller.enqueue_entry(bad_entry)


async def test_enqueue_entry_overrides_audio_language(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path],
) -> None:
    controller, _, _ = workflow_controller_setup
    entries = await controller.list_entries(refresh=True)
    workflow = await controller.enqueue_entry(
        entries[0],
        audio_language="pt-BR",
    )
    assert workflow.pre_settings.audio_language == "pt-BR"


async def test_enqueue_entry_can_override_voice_changer(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path],
) -> None:
    controller, _, _ = workflow_controller_setup
    entries = await controller.list_entries(refresh=True)
    workflow = await controller.enqueue_entry(
        entries[0],
        voice_changer=VoiceChangerSettings(
            voice_id="voice_new",
            model_id="custom-model",
            remove_background_noise=False,
            voice_settings=VoiceSettings(stability=0.7, similarity_boost=0.8),
        ),
        set_voice_changer=True,
    )
    assert workflow.pre_settings.voice_changer is not None
    assert workflow.pre_settings.voice_changer.voice_id == "voice_new"
    assert workflow.pre_settings.voice_changer.model_id == "custom-model"
    assert workflow.pre_settings.voice_changer.remove_background_noise is False
    assert workflow.pre_settings.voice_changer.voice_settings == VoiceSettings(
        stability=0.7,
        similarity_boost=0.8,
    )


async def test_enqueue_entry_can_disable_voice_changer(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path],
) -> None:
    controller, _, workflows_dir = workflow_controller_setup
    payload = {
        "workflow": "voice changer on",
        "pre_settings": {
            "model_creation": {"method": "catalog", "asset_kind": "generated", "asset_id": "x"},
            "voice_changer": {"voice_id": "voice_original", "model_id": "keep-me"},
        },
        "run": [
            {
                "step": 1,
                "scene_name": "Hook",
                "type": "a-roll",
                "prompt": "Persona hablando a cámara",
                "text": "Hola",
            }
        ],
    }
    path = workflows_dir / "voice_changer_disable.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    entries = await controller.list_entries(refresh=True)
    target = next(entry for entry in entries if entry.path.name == "voice_changer_disable.json")
    workflow = await controller.enqueue_entry(target, voice_changer=None, set_voice_changer=True)
    assert workflow.pre_settings.voice_changer is None


async def test_recreate_step_resets_completed_step_and_requeues(
    tmp_settings: Settings,
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path],
) -> None:
    controller, fake_runner, _ = workflow_controller_setup
    entries = await controller.list_entries(refresh=True)
    workflow = await controller.enqueue_entry(entries[0])
    loaded = await controller.get_workflow(workflow.id)
    assert loaded is not None
    step = loaded.steps[0]
    output_dir = Path(loaded.output_dir)
    step_dir = output_dir / f"step_{step.step:02d}_{step.scene_slug}"
    step_dir.mkdir(parents=True, exist_ok=True)
    video_path = step_dir / "video.mp4"
    video_path.write_bytes(b"old video")
    final_video = output_dir / "final.mp4"
    final_audio = output_dir / "final_audio.mp3"
    voice_changed = output_dir / "voice_changed_audio.mp3"
    final_video.write_bytes(b"old final")
    final_audio.write_bytes(b"old audio")
    voice_changed.write_bytes(b"old voice")

    loaded.status = WorkflowStatus.COMPLETED
    step.status = WorkflowStepStatus.COMPLETED
    step.video_task_id = "veo_old"
    step.video_path = str(video_path)
    step.progress = {
        WorkflowProgressKey.VIDEO: WorkflowProgressStatus.COMPLETED,
        WorkflowProgressKey.DOWNLOAD: WorkflowProgressStatus.COMPLETED,
    }
    repo = WorkflowDB(tmp_settings.db_path)
    await repo.upsert_workflow(loaded)

    recreated = await controller.recreate_step(loaded.id, step.step)

    recreated_step = recreated.steps[0]
    assert recreated.status == WorkflowStatus.QUEUED
    assert recreated_step.status == WorkflowStepStatus.QUEUED
    assert recreated_step.video_task_id is None
    assert recreated_step.video_path is None
    assert recreated_step.progress == {}
    assert not video_path.exists()
    assert not final_video.exists()
    assert not final_audio.exists()
    assert not voice_changed.exists()
    assert any(run.id == loaded.id for run in fake_runner.runs)
    persisted = await controller.get_workflow(loaded.id)
    assert persisted is not None
    assert persisted.steps[0].status == WorkflowStepStatus.QUEUED


async def test_recreate_step_rejects_non_terminal_workflow(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path],
) -> None:
    controller, _, _ = workflow_controller_setup
    entries = await controller.list_entries(refresh=True)
    workflow = await controller.enqueue_entry(entries[0])
    with pytest.raises(WorkflowValidationError, match="estado terminal"):
        await controller.recreate_step(workflow.id, 1)


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


def _stale_product_ref() -> ImageAssetRef:
    return ImageAssetRef(
        kind=ImageAssetKind.UPLOADED,
        id="uploads/stale_product.png",
        label="producto.png",
        kie_url="https://tempfile.kie.ai/uploads/stale_product.png",
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )


def _build_controller_with_fake_resolver(
    tmp_settings: Settings,
    db: WorkflowDB,
    images_db: ImagesDB,
    generated_db: GeneratedImagesDB,
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
        AtomicWorkflowManifestWriter(tmp_settings.outputs_dir),
        queue,
        fake_resolver,  # type: ignore[arg-type]
        scan_loader=lambda: scan_workflows_dir(workflows_dir),
        entry_builder=build_workflow_from_entry,
        uploaded_images=images_db,
        generated_images=generated_db,
    )
    return controller, fake_runner


async def test_preview_base_from_prompt_invokes_resolver_and_downloads(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path],
    tmp_settings: Settings,
) -> None:
    """`preview_base_from_prompt` debe llamar al resolver y producir el path."""
    _, _, workflows_dir = workflow_controller_setup
    fake_resolver = _FakeBaseResolver(preview_ref=_mock_generated_ref())
    db = WorkflowDB(tmp_settings.db_path)
    await db.init()
    images_db = ImagesDB(tmp_settings.db_path)
    await images_db.init()
    generated_db = GeneratedImagesDB(tmp_settings.db_path)
    await generated_db.init()
    controller, _ = _build_controller_with_fake_resolver(
        tmp_settings, db, images_db, generated_db, workflows_dir, fake_resolver
    )
    ref, path = await controller.preview_base_from_prompt(
        "A photorealistic woman", label_hint="test_wf"
    )
    assert ref.id == "img_mock"
    assert path.parent.name == "_previews"
    assert path.is_file()  # el fake resolver escribió bytes ahí
    assert fake_resolver.preview_calls == [("A photorealistic woman", "test_wf", path)]


async def test_upload_local_base_propagates_ref(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path],
    tmp_settings: Settings,
    tmp_path: Path,
) -> None:
    """`upload_local_base` debe propagar la ref devuelta por el resolver."""
    _, _, workflows_dir = workflow_controller_setup
    fake_resolver = _FakeBaseResolver(upload_ref=_mock_uploaded_ref())
    db = WorkflowDB(tmp_settings.db_path)
    await db.init()
    images_db = ImagesDB(tmp_settings.db_path)
    await images_db.init()
    generated_db = GeneratedImagesDB(tmp_settings.db_path)
    await generated_db.init()
    controller, _ = _build_controller_with_fake_resolver(
        tmp_settings, db, images_db, generated_db, workflows_dir, fake_resolver
    )
    fake_image = tmp_path / "fake.png"
    fake_image.write_bytes(b"fake")
    ref = await controller.upload_local_base(fake_image)
    assert ref.kind == ImageAssetKind.UPLOADED
    assert fake_resolver.upload_calls == [fake_image]


async def test_ensure_product_ready_for_retry_requires_manual_reload_when_local_is_invalid(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path],
) -> None:
    controller, _, workflows_dir = workflow_controller_setup
    path = workflows_dir / "product_retry_manual.json"
    path.write_text(json.dumps(_valid_product_payload("Product Retry Manual")), encoding="utf-8")
    entries = await controller.list_entries(refresh=True)
    entry = next(e for e in entries if e.path.name == "product_retry_manual.json")
    workflow = await controller.enqueue_entry(
        entry,
        product_ref=_stale_product_ref(),
        product_local_path="/tmp/no-existe-producto.png",
    )
    ready = await controller.ensure_product_ready_for_retry(workflow.id)
    assert ready is False


async def test_ensure_product_ready_for_retry_reuploads_when_local_exists(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path],
    tmp_settings: Settings,
    tmp_path: Path,
) -> None:
    _, _, workflows_dir = workflow_controller_setup
    path = workflows_dir / "product_retry_auto.json"
    path.write_text(json.dumps(_valid_product_payload("Product Retry Auto")), encoding="utf-8")
    fake_resolver = _FakeBaseResolver(upload_ref=_mock_uploaded_ref())
    db = WorkflowDB(tmp_settings.db_path)
    await db.init()
    images_db = ImagesDB(tmp_settings.db_path)
    await images_db.init()
    generated_db = GeneratedImagesDB(tmp_settings.db_path)
    await generated_db.init()
    controller, _ = _build_controller_with_fake_resolver(
        tmp_settings, db, images_db, generated_db, workflows_dir, fake_resolver
    )
    entries = await controller.list_entries(refresh=True)
    entry = next(e for e in entries if e.path.name == "product_retry_auto.json")
    product_file = tmp_path / "producto.png"
    product_file.write_bytes(b"\x89PNG\r\n\x1a\n")
    workflow = await controller.enqueue_entry(
        entry,
        product_ref=_stale_product_ref(),
        product_local_path=str(product_file),
    )
    ready = await controller.ensure_product_ready_for_retry(workflow.id)
    assert ready is True
    assert fake_resolver.upload_calls == [product_file]
    loaded = await controller.get_workflow(workflow.id)
    assert loaded is not None
    assert loaded.pre_settings.product_image is not None
    assert loaded.pre_settings.product_image.resolved_image_ref is not None
    assert loaded.pre_settings.product_image.resolved_image_ref.id == _mock_uploaded_ref().id


async def test_replace_workflow_product_persists_new_product_ref(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path],
    tmp_settings: Settings,
    tmp_path: Path,
) -> None:
    _, _, workflows_dir = workflow_controller_setup
    path = workflows_dir / "product_replace.json"
    path.write_text(json.dumps(_valid_product_payload("Product Replace")), encoding="utf-8")
    fake_resolver = _FakeBaseResolver(upload_ref=_mock_uploaded_ref())
    db = WorkflowDB(tmp_settings.db_path)
    await db.init()
    images_db = ImagesDB(tmp_settings.db_path)
    await images_db.init()
    generated_db = GeneratedImagesDB(tmp_settings.db_path)
    await generated_db.init()
    controller, _ = _build_controller_with_fake_resolver(
        tmp_settings, db, images_db, generated_db, workflows_dir, fake_resolver
    )
    entries = await controller.list_entries(refresh=True)
    entry = next(e for e in entries if e.path.name == "product_replace.json")
    workflow = await controller.enqueue_entry(entry)
    new_product = tmp_path / "nuevo_producto.png"
    new_product.write_bytes(b"\x89PNG\r\n\x1a\n")
    updated = await controller.replace_workflow_product(workflow.id, new_product)
    assert updated.pre_settings.product_image is not None
    assert updated.pre_settings.product_image.local_path == str(new_product)
    assert updated.pre_settings.product_image.resolved_image_ref is not None
    assert updated.pre_settings.product_image.resolved_image_ref.id == _mock_uploaded_ref().id
    loaded = await controller.get_workflow(workflow.id)
    assert loaded is not None
    assert loaded.pre_settings.product_image is not None
    assert loaded.pre_settings.product_image.local_path == str(new_product)


async def test_enqueue_with_resolved_base_ref_persists_and_skips_path_validation(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path],
) -> None:
    """`enqueue_entry(resolved_base_ref=...)` debe saltar validate_local_model_path."""
    controller, _, _ = workflow_controller_setup
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
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path],
) -> None:
    """Sin `resolved_base_ref`, el validator del path se ejecuta normalmente."""
    from kie_avatar_studio.domain.errors import ImageValidationError

    controller, _, workflows_dir = workflow_controller_setup
    # Sobreescribimos el JSON con method=local + local_path inexistente.
    bad_payload = {
        "workflow": "Bad Local",
        "pre_settings": {
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
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path],
) -> None:
    controller, _, _ = workflow_controller_setup
    ok = await controller.cancel("wf_does_not_exist")
    assert not ok


async def test_retry_returns_false_for_unknown_id(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path],
) -> None:
    controller, _, _ = workflow_controller_setup
    assert not await controller.retry("wf_does_not_exist")


async def test_retry_completed_requeues_when_final_artifacts_are_missing(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path],
) -> None:
    from kie_avatar_studio.domain.models import WorkflowStatus, WorkflowStepStatus

    controller, _, _ = workflow_controller_setup
    entries = await controller.list_entries(refresh=True)
    workflow = await controller.enqueue_entry(entries[0])
    step = workflow.step_by_number(1)
    assert step is not None
    output_dir = Path(workflow.output_dir)
    step_dir = output_dir / f"step_{step.step:02d}_{step.scene_slug}"
    step_dir.mkdir(parents=True, exist_ok=True)
    video_path = step_dir / "video.mp4"
    video_path.write_bytes(b"fake video")
    step.video_path = str(video_path)
    step.status = WorkflowStepStatus.COMPLETED
    step.completed_at = datetime.now(UTC)
    workflow.status = WorkflowStatus.COMPLETED
    workflow.error = None
    await controller._repository.upsert_step(workflow.id, step)  # type: ignore[attr-defined]
    await controller._repository.update_workflow_header(workflow)  # type: ignore[attr-defined]
    assert not (output_dir / "final.mp4").exists()
    assert not (output_dir / "final_audio.mp3").exists()

    ok = await controller.retry(workflow.id)
    assert ok is True
    loaded = await controller.get_workflow(workflow.id)
    assert loaded is not None
    assert loaded.status == WorkflowStatus.QUEUED


async def test_retry_completed_returns_false_when_final_artifacts_exist(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path],
) -> None:
    from kie_avatar_studio.domain.models import WorkflowStatus, WorkflowStepStatus

    controller, _, _ = workflow_controller_setup
    entries = await controller.list_entries(refresh=True)
    workflow = await controller.enqueue_entry(entries[0])
    step = workflow.step_by_number(1)
    assert step is not None
    output_dir = Path(workflow.output_dir)
    step_dir = output_dir / f"step_{step.step:02d}_{step.scene_slug}"
    step_dir.mkdir(parents=True, exist_ok=True)
    video_path = step_dir / "video.mp4"
    video_path.write_bytes(b"fake video")
    (output_dir / "final.mp4").write_bytes(b"joined")
    (output_dir / "final_audio.mp3").write_bytes(b"audio")
    step.video_path = str(video_path)
    step.status = WorkflowStepStatus.COMPLETED
    step.completed_at = datetime.now(UTC)
    workflow.status = WorkflowStatus.COMPLETED
    workflow.error = None
    await controller._repository.upsert_step(workflow.id, step)  # type: ignore[attr-defined]
    await controller._repository.update_workflow_header(workflow)  # type: ignore[attr-defined]

    ok = await controller.retry(workflow.id)
    assert ok is False
    loaded = await controller.get_workflow(workflow.id)
    assert loaded is not None
    assert loaded.status == WorkflowStatus.COMPLETED


async def test_list_workflows_returns_persisted_in_db(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path],
) -> None:
    controller, _, _ = workflow_controller_setup
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
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path],
) -> None:
    from kie_avatar_studio.domain.models import WorkflowStatus, WorkflowStepStatus

    controller, _fake_runner, _ = workflow_controller_setup
    entries = await controller.list_entries(refresh=True)
    workflow = await _enqueue_awaiting_workflow(controller, entries)
    result = await controller.approve_scene(workflow.id, 1)
    step = result.step_by_number(1)
    assert step is not None
    assert step.scene_image_approved_at is not None
    assert step.status == WorkflowStepStatus.QUEUED
    assert result.status == WorkflowStatus.QUEUED


async def test_regenerate_scene_resets_and_requeues(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path],
) -> None:
    from kie_avatar_studio.domain.models import WorkflowStatus, WorkflowStepStatus
    from kie_avatar_studio.domain.workflow_artifacts import (
        workflow_final_audio_filename,
        workflow_final_video_filename,
    )

    controller, _fake_runner, _ = workflow_controller_setup
    entries = await controller.list_entries(refresh=True)
    workflow = await _enqueue_awaiting_workflow(controller, entries)
    step_before = workflow.step_by_number(1)
    assert step_before is not None
    output_dir = Path(workflow.output_dir)
    step_dir = output_dir / f"step_{step_before.step:02d}_{step_before.scene_slug}"
    step_dir.mkdir(parents=True, exist_ok=True)
    old_video = step_dir / f"step_{step_before.step:02d}_{step_before.scene_slug}_video.mp4"
    old_video.write_bytes(b"old video")
    final_video = output_dir / workflow_final_video_filename(workflow.slug)
    final_audio = output_dir / workflow_final_audio_filename(workflow.slug)
    final_video.write_bytes(b"old final")
    final_audio.write_bytes(b"old audio")
    step_before.video_path = str(old_video)
    step_before.video_task_id = "veo_old"
    await controller._repository.upsert_step(workflow.id, step_before)  # type: ignore[attr-defined]

    result = await controller.regenerate_scene(
        workflow.id,
        1,
        scene_description="Nueva cocina luminosa",
        prompt="Nuevo prompt visual",
        product_prompt="Nuevo producto en mesa",
        text="Nueva voz exacta",
    )
    step = result.step_by_number(1)
    assert step is not None
    assert step.bg_image_job_id is None
    assert step.scene_image_path is None
    assert step.scene_image_approved_at is None
    assert step.video_task_id is None
    assert step.video_path is None
    assert step.scene_description == "Nueva cocina luminosa"
    assert step.prompt == "Nuevo prompt visual"
    assert step.product_prompt == "Nuevo producto en mesa"
    assert step.text == "Nueva voz exacta"
    assert step.status == WorkflowStepStatus.QUEUED
    assert result.status == WorkflowStatus.QUEUED
    assert not old_video.exists()
    assert not final_video.exists()
    assert not final_audio.exists()


async def test_cancel_step_marks_cancelled_and_continues_workflow(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path],
) -> None:
    from kie_avatar_studio.domain.models import WorkflowStatus, WorkflowStepStatus

    controller, _, _ = workflow_controller_setup
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
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path],
) -> None:
    from kie_avatar_studio.domain.errors import WorkflowValidationError

    controller, _, _ = workflow_controller_setup
    entries = await controller.list_entries(refresh=True)
    workflow = await controller.enqueue_entry(entries[0])
    # Step queda en QUEUED (no AWAITING_APPROVAL). Aprobar debe fallar.
    with pytest.raises(WorkflowValidationError, match="no está esperando aprobación"):
        await controller.approve_scene(workflow.id, 1)


async def test_approve_scene_rejects_unknown_workflow(
    workflow_controller_setup: tuple[WorkflowController, _FakeRunner, Path],
) -> None:
    from kie_avatar_studio.domain.errors import WorkflowNotFoundError

    controller, _, _ = workflow_controller_setup
    with pytest.raises(WorkflowNotFoundError):
        await controller.approve_scene("wf_does_not_exist", 1)
