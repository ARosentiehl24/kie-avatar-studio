"""Tests del `WorkflowDB` (persistencia de workflow + steps)."""

from __future__ import annotations

import pytest

from kie_avatar_studio.config import Settings
from kie_avatar_studio.domain.models import (
    ImageAssetKind,
    ModelCreation,
    ModelCreationMethod,
    StepType,
    WorkflowJob,
    WorkflowPreSettings,
    WorkflowProgressKey,
    WorkflowProgressStatus,
    WorkflowStatus,
    WorkflowStep,
    WorkflowStepStatus,
)
from kie_avatar_studio.infra.workflow_db import WorkflowDB


def _make_pre_settings() -> WorkflowPreSettings:
    return WorkflowPreSettings(
        audio_language="es-419",
        voice_preset_id="latina_warm",
        model_creation=ModelCreation(method=ModelCreationMethod.PROMPT, prompt="A woman"),
    )


def _make_step(step: int = 1, type_: StepType = StepType.A_ROLL) -> WorkflowStep:
    return WorkflowStep(
        step=step,
        scene_name=f"Escena {step}",
        scene_slug=f"escena_{step}",
        type=type_,
        change_scene=False,
        scene_description="",
        prompt="prompt",
        text="hola" if type_ == StepType.A_ROLL else "",
    )


def _make_workflow(steps: list[WorkflowStep] | None = None) -> WorkflowJob:
    return WorkflowJob(
        id="wf_test_001",
        name="Test Workflow",
        slug="test_workflow",
        source_json_path="workflows/test.json",
        output_dir="outputs/wf_test_001",
        pre_settings=_make_pre_settings(),
        steps=steps or [_make_step(1), _make_step(2, StepType.B_ROLL)],
    )


@pytest.fixture
async def workflow_db(tmp_settings: Settings) -> WorkflowDB:
    db = WorkflowDB(tmp_settings.db_path)
    await db.init()
    return db


async def test_init_creates_tables(workflow_db: WorkflowDB) -> None:
    # Si init() corrió OK, podemos upsert sin error.
    workflow = _make_workflow()
    await workflow_db.upsert_workflow(workflow)
    loaded = await workflow_db.get(workflow.id)
    assert loaded is not None


async def test_upsert_and_get_roundtrip(workflow_db: WorkflowDB) -> None:
    workflow = _make_workflow()
    await workflow_db.upsert_workflow(workflow)
    loaded = await workflow_db.get(workflow.id)
    assert loaded is not None
    assert loaded.id == workflow.id
    assert loaded.name == "Test Workflow"
    assert loaded.slug == "test_workflow"
    assert len(loaded.steps) == 2
    assert loaded.steps[0].type == StepType.A_ROLL
    assert loaded.steps[1].type == StepType.B_ROLL
    assert loaded.pre_settings.voice_preset_id == "latina_warm"


async def test_get_returns_none_for_unknown_id(workflow_db: WorkflowDB) -> None:
    assert await workflow_db.get("wf_nope") is None


async def test_upsert_step_only_updates_one_row(workflow_db: WorkflowDB) -> None:
    workflow = _make_workflow()
    await workflow_db.upsert_workflow(workflow)
    # Modificamos solo el step 2 con progress y status.
    step2 = workflow.steps[1]
    step2.status = WorkflowStepStatus.PREPARING
    step2.progress[WorkflowProgressKey.SCENE_IMAGE] = WorkflowProgressStatus.RUNNING
    await workflow_db.upsert_step(workflow.id, step2)

    loaded = await workflow_db.get(workflow.id)
    assert loaded is not None
    # Step 1 sigue como QUEUED, step 2 como PREPARING.
    assert loaded.steps[0].status == WorkflowStepStatus.QUEUED
    assert loaded.steps[1].status == WorkflowStepStatus.PREPARING
    # Progress se persistió y deserializó como enum.
    assert (
        loaded.steps[1].progress[WorkflowProgressKey.SCENE_IMAGE] == WorkflowProgressStatus.RUNNING
    )


async def test_update_workflow_header_does_not_touch_steps(workflow_db: WorkflowDB) -> None:
    workflow = _make_workflow()
    await workflow_db.upsert_workflow(workflow)
    # Mutamos solo header (status, error).
    workflow.status = WorkflowStatus.RUNNING
    workflow.error = "some error"
    workflow.manifest_write_failed = True
    await workflow_db.update_workflow_header(workflow)

    loaded = await workflow_db.get(workflow.id)
    assert loaded is not None
    assert loaded.status == WorkflowStatus.RUNNING
    assert loaded.error == "some error"
    assert loaded.manifest_write_failed
    # Steps sin cambio.
    assert all(s.status == WorkflowStepStatus.QUEUED for s in loaded.steps)


async def test_progress_json_roundtrip_with_all_keys(workflow_db: WorkflowDB) -> None:
    """Verifica que progress con varias keys serializa/deserializa correctamente."""
    workflow = _make_workflow()
    step = workflow.steps[0]
    step.progress = {
        WorkflowProgressKey.SCENE_IMAGE: WorkflowProgressStatus.COMPLETED,
        WorkflowProgressKey.AUDIO: WorkflowProgressStatus.RUNNING,
        WorkflowProgressKey.VIDEO: WorkflowProgressStatus.PENDING,
        WorkflowProgressKey.DOWNLOAD: WorkflowProgressStatus.PENDING,
    }
    await workflow_db.upsert_workflow(workflow)
    loaded = await workflow_db.get(workflow.id)
    assert loaded is not None
    assert (
        loaded.steps[0].progress[WorkflowProgressKey.SCENE_IMAGE]
        == WorkflowProgressStatus.COMPLETED
    )
    assert loaded.steps[0].progress[WorkflowProgressKey.AUDIO] == WorkflowProgressStatus.RUNNING


async def test_list_by_status_filters_correctly(workflow_db: WorkflowDB) -> None:
    wf_a = _make_workflow()
    wf_a.id = "wf_001"
    wf_b = _make_workflow()
    wf_b.id = "wf_002"
    wf_b.status = WorkflowStatus.RUNNING
    await workflow_db.upsert_workflow(wf_a)
    await workflow_db.upsert_workflow(wf_b)

    queued = await workflow_db.list_by_status(WorkflowStatus.QUEUED)
    running = await workflow_db.list_by_status(WorkflowStatus.RUNNING)
    assert len(queued) == 1
    assert queued[0].id == "wf_001"
    assert len(running) == 1
    assert running[0].id == "wf_002"


async def test_list_recent_orders_by_created_at_desc(workflow_db: WorkflowDB) -> None:
    wf_a = _make_workflow()
    wf_a.id = "wf_a"
    await workflow_db.upsert_workflow(wf_a)

    # Esperá un tick para que el created_at sea distinto.
    import asyncio

    await asyncio.sleep(0.01)
    wf_b = _make_workflow()
    wf_b.id = "wf_b"
    await workflow_db.upsert_workflow(wf_b)

    recent = await workflow_db.list_recent(limit=10)
    ids = [w.id for w in recent]
    assert ids == ["wf_b", "wf_a"]


async def test_delete_removes_workflow_and_steps(workflow_db: WorkflowDB) -> None:
    workflow = _make_workflow()
    await workflow_db.upsert_workflow(workflow)
    await workflow_db.delete(workflow.id)
    assert await workflow_db.get(workflow.id) is None


async def test_upsert_workflow_is_idempotent(workflow_db: WorkflowDB) -> None:
    """Re-upsertar el mismo workflow no duplica filas y mantiene los steps."""
    workflow = _make_workflow()
    await workflow_db.upsert_workflow(workflow)
    # Cambiamos algo y re-upsertamos.
    workflow.name = "Updated Name"
    await workflow_db.upsert_workflow(workflow)
    loaded = await workflow_db.get(workflow.id)
    assert loaded is not None
    assert loaded.name == "Updated Name"
    assert len(loaded.steps) == 2


async def test_step_outputs_paths_persist(workflow_db: WorkflowDB) -> None:
    workflow = _make_workflow()
    workflow.steps[0].scene_image_path = "outputs/wf/step_01/scene.png"
    workflow.steps[0].video_path = "outputs/wf/step_01/final.mp4"
    workflow.steps[1].audio_path = "outputs/wf/step_02/audio.mp3"
    await workflow_db.upsert_workflow(workflow)
    loaded = await workflow_db.get(workflow.id)
    assert loaded is not None
    assert loaded.steps[0].scene_image_path == "outputs/wf/step_01/scene.png"
    assert loaded.steps[0].video_path == "outputs/wf/step_01/final.mp4"
    assert loaded.steps[1].audio_path == "outputs/wf/step_02/audio.mp3"


async def test_resolved_image_ref_in_pre_settings_roundtrips(workflow_db: WorkflowDB) -> None:
    """`pre_settings.model_creation.resolved_image_ref` debe persistir si lo setea el runner."""
    from datetime import UTC, datetime

    from kie_avatar_studio.domain.models import ImageAssetRef

    workflow = _make_workflow()
    workflow.pre_settings.model_creation.resolved_image_ref = ImageAssetRef(
        kind=ImageAssetKind.GENERATED,
        id="img_xyz",
        label="modelo base",
        kie_url="https://tempfile.kie.ai/base.png",
        expires_at=datetime.now(UTC),
    )
    await workflow_db.upsert_workflow(workflow)
    loaded = await workflow_db.get(workflow.id)
    assert loaded is not None
    ref = loaded.pre_settings.model_creation.resolved_image_ref
    assert ref is not None
    assert ref.id == "img_xyz"
    assert ref.kie_url == "https://tempfile.kie.ai/base.png"


async def test_roundtrip_include_product_and_product_prompt(workflow_db: WorkflowDB) -> None:
    """Los campos de producto por step sobreviven el round-trip a la DB."""
    step = _make_step(1, StepType.A_ROLL)
    step.include_product = True
    step.product_prompt = "Sostiene el frasco a la altura del pecho"
    workflow = _make_workflow(steps=[step])
    await workflow_db.upsert_workflow(workflow)
    loaded = await workflow_db.get(workflow.id)
    assert loaded is not None
    assert loaded.steps[0].include_product is True
    assert loaded.steps[0].product_prompt == "Sostiene el frasco a la altura del pecho"


async def test_roundtrip_promote_product_and_product_image(workflow_db: WorkflowDB) -> None:
    """`promote_product` + `product_image` (en pre_settings_json) sobreviven el round-trip."""
    from datetime import UTC, datetime

    from kie_avatar_studio.domain.models import ImageAssetRef, ProductImage

    workflow = _make_workflow()
    workflow.pre_settings.promote_product = True
    workflow.pre_settings.product_image = ProductImage(
        local_path="inputs/product.png",
        resolved_image_ref=ImageAssetRef(
            kind=ImageAssetKind.UPLOADED,
            id="uploads/product.png",
            label="product.png",
            kie_url="https://tempfile.kie.ai/product.png",
            expires_at=datetime.now(UTC),
        ),
    )
    await workflow_db.upsert_workflow(workflow)
    loaded = await workflow_db.get(workflow.id)
    assert loaded is not None
    assert loaded.pre_settings.promote_product is True
    product = loaded.pre_settings.product_image
    assert product is not None
    assert product.local_path == "inputs/product.png"
    assert product.resolved_image_ref is not None
    assert product.resolved_image_ref.kie_url == "https://tempfile.kie.ai/product.png"


async def test_step_duration_seconds_persisted_roundtrip(workflow_db: WorkflowDB) -> None:
    """`step.duration_seconds` debe sobrevivir el roundtrip por DB.

    Regresión del hallazgo CR-6.3: el campo se agregó al domain pero el
    schema/_UPSERT/_step_to_row/_row_to_step debían actualizarse a la
    par. Sin esto, los b-roll con `duration_seconds=10` del JSON se
    persistían como `None` → en restore tras crash el runner caía al
    default y descartaba la decisión del autor del workflow.
    """
    workflow = _make_workflow(
        steps=[
            _make_step(1, StepType.A_ROLL),
            _make_step(2, StepType.B_ROLL),
            _make_step(3, StepType.B_ROLL),
        ]
    )
    # b-roll #2 con override 10, b-roll #3 sin override (None)
    workflow.steps[1].duration_seconds = 10
    workflow.steps[2].duration_seconds = None
    await workflow_db.upsert_workflow(workflow)
    loaded = await workflow_db.get(workflow.id)
    assert loaded is not None
    assert loaded.steps[0].duration_seconds is None  # a-roll, sin override
    assert loaded.steps[1].duration_seconds == 10  # b-roll con override
    assert loaded.steps[2].duration_seconds is None  # b-roll sin override


async def test_step_duration_seconds_updates_via_upsert_step(workflow_db: WorkflowDB) -> None:
    """Mutar `duration_seconds` y reupsertar el step persiste el cambio."""
    workflow = _make_workflow(steps=[_make_step(1, StepType.B_ROLL)])
    workflow.steps[0].duration_seconds = 5
    await workflow_db.upsert_workflow(workflow)
    # Cambio en runtime (ej. UI lo reasigna a 10).
    workflow.steps[0].duration_seconds = 10
    await workflow_db.upsert_step(workflow.id, workflow.steps[0])
    loaded = await workflow_db.get(workflow.id)
    assert loaded is not None
    assert loaded.steps[0].duration_seconds == 10


async def test_pre_settings_i2v_duration_override_persisted(workflow_db: WorkflowDB) -> None:
    """`pre_settings.i2v_duration_seconds` (override del workflow) debe persistir."""
    workflow = _make_workflow()
    workflow.pre_settings.i2v_duration_seconds = 10
    await workflow_db.upsert_workflow(workflow)
    loaded = await workflow_db.get(workflow.id)
    assert loaded is not None
    assert loaded.pre_settings.i2v_duration_seconds == 10


async def test_roundtrip_step_image_aspect_ratio(workflow_db: WorkflowDB) -> None:
    """`step.image_aspect_ratio` debe sobrevivir el roundtrip por DB."""
    workflow = _make_workflow()
    workflow.steps[0].image_aspect_ratio = "9:16"
    await workflow_db.upsert_workflow(workflow)
    loaded = await workflow_db.get(workflow.id)
    assert loaded is not None
    assert loaded.steps[0].image_aspect_ratio == "9:16"


async def test_roundtrip_step_include_model(workflow_db: WorkflowDB) -> None:
    """`step.include_model` debe sobrevivir el roundtrip por DB (default es True, probamos False)."""
    workflow = _make_workflow()
    workflow.steps[0].include_model = False
    await workflow_db.upsert_workflow(workflow)
    loaded = await workflow_db.get(workflow.id)
    assert loaded is not None
    assert loaded.steps[0].include_model is False

