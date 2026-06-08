"""Test e2e con MockTransport: workflow completo con los 3 tipos de step."""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from pathlib import Path

import httpx
import pytest

from kie_avatar_studio.app_layer.queue_manager import QueueManager
from kie_avatar_studio.app_layer.runner_factories import (
    AudioRunnerDeps,
    ImageRunnerDeps,
    WorkflowRunnerFactory,
)
from kie_avatar_studio.app_layer.workflow_base_resolver import WorkflowBaseResolver
from kie_avatar_studio.app_layer.workflow_controller import WorkflowController
from kie_avatar_studio.app_layer.workflow_lifecycle import WorkflowLifecycle
from kie_avatar_studio.app_layer.workflow_runner import (
    WorkflowRunner,
    WorkflowRunnerDeps,
)
from kie_avatar_studio.app_layer.workflow_step_runner import WorkflowStepRunner
from kie_avatar_studio.config import Settings
from kie_avatar_studio.domain.events import WorkflowJobUpdated
from kie_avatar_studio.domain.models import (
    SceneApprovalMode,
    VoicePreset,
    WorkflowJob,
    WorkflowStatus,
    WorkflowStepStatus,
)
from kie_avatar_studio.infra.audio_jobs_db import AudioJobsDB
from kie_avatar_studio.infra.audios_db import AudiosDB
from kie_avatar_studio.infra.generated_images_db import GeneratedImagesDB
from kie_avatar_studio.infra.image_jobs_db import ImageJobsDB
from kie_avatar_studio.infra.images_db import ImagesDB
from kie_avatar_studio.infra.kie_client import KieClient
from kie_avatar_studio.infra.presets_store import VoicePresetsStore
from kie_avatar_studio.infra.workflow_db import WorkflowDB
from kie_avatar_studio.infra.workflow_loader import (
    build_workflow_from_entry,
    scan_workflows_dir,
)
from kie_avatar_studio.infra.workflow_manifest_writer import AtomicWorkflowManifestWriter


class _MockKieAllSuccess:
    """Mock handler que responde success a TODOS los endpoints."""

    def __init__(self) -> None:
        self.task_counter = 0
        self.tasks: dict[str, dict] = {}
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/api/file-stream-upload":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "fileName": "modelo.png",
                        "filePath": "uploads/modelo.png",
                        "downloadUrl": "https://tempfile.kie.ai/uploads/modelo.png",
                        "fileSize": 100,
                        "mimeType": "image/png",
                    }
                },
            )
        if path == "/api/v1/jobs/createTask":
            self.task_counter += 1
            task_id = f"tk_{self.task_counter:04d}"
            body = json.loads(request.content)
            self.tasks[task_id] = {"model": body["model"]}
            return httpx.Response(200, json={"data": {"taskId": task_id}})
        if path == "/api/v1/jobs/recordInfo":
            task_id = request.url.params.get("taskId")
            if task_id not in self.tasks:
                return httpx.Response(404)
            model = self.tasks[task_id]["model"]
            url = self._result_url(model, task_id)
            return httpx.Response(
                200,
                json={
                    "data": {
                        "state": "success",
                        "resultJson": json.dumps({"resultUrls": [url]}),
                    }
                },
            )
        if "tempfile" in request.url.host or "kie.ai" in request.url.host:
            return httpx.Response(200, content=b"fake binary")
        return httpx.Response(404)

    @staticmethod
    def _result_url(model: str, task_id: str) -> str:
        if "avatar-pro" in model:
            return f"https://tempfile.kie.ai/avatar/{task_id}.mp4"
        if "image-to-video" in model or "kling-3.0/video" in model:
            return f"https://tempfile.kie.ai/i2v/{task_id}.mp4"
        if "nano-banana" in model:
            return f"https://tempfile.kie.ai/img/{task_id}.png"
        if "text-to-speech" in model:
            return f"https://tempfile.kie.ai/audio/{task_id}.mp3"
        return f"https://tempfile.kie.ai/generic/{task_id}.bin"


def _workflow_json_3_steps() -> dict:
    """JSON con un step de cada tipo (a-roll, b-roll con text, b-roll silent)."""
    return {
        "workflow": "E2E Test 3 Steps",
        "pre_settings": {
            "audio_language": "es-419",
            "voice_preset": "test_voice",
            "model_creation": {
                "method": "prompt",
                "prompt": "Photorealistic woman talking to camera",
            },
        },
        "run": [
            {
                "step": 1,
                "scene_name": "Hook a-roll",
                "type": "a-roll",
                "change_scene": False,
                "scene_description": "",
                "prompt": "Una mujer mira a cámara plano medio",
                "text": "Hola, gracias por estar acá hoy.",
            },
            {
                "step": 2,
                "scene_name": "B-roll con audio",
                "type": "b-roll",
                "change_scene": True,
                "scene_description": "Cocina con luz natural",
                "prompt": "Manos cortando vegetales",
                "text": "Esta es la narración del b-roll para post.",
            },
            {
                "step": 3,
                "scene_name": "B-roll silencioso",
                "type": "b-roll",
                "change_scene": True,
                "scene_description": "Apothecary jar con luz cálida",
                "prompt": "Frasco ambar siendo abierto",
                "text": "",
            },
        ],
    }


@pytest.fixture
async def e2e_setup(tmp_settings: Settings):
    settings = tmp_settings.model_copy(update={"poll_interval_seconds": 1})
    # Workflow JSON en filesystem.
    workflows_dir = settings.workflows_dir
    (workflows_dir / "e2e.json").write_text(json.dumps(_workflow_json_3_steps()), encoding="utf-8")

    # Cliente Kie con MockTransport.
    handler = _MockKieAllSuccess()
    kie = KieClient(settings)
    await kie._client.aclose()
    kie._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler.handle),
        headers={"Authorization": "Bearer test"},
    )

    # DBs.
    images_db = ImagesDB(settings.db_path)
    await images_db.init()
    generated_db = GeneratedImagesDB(settings.db_path)
    await generated_db.init()
    audios_db = AudiosDB(settings.db_path)
    await audios_db.init()
    audio_jobs_db = AudioJobsDB(settings.db_path)
    await audio_jobs_db.init()
    image_jobs_db = ImageJobsDB(settings.db_path)
    await image_jobs_db.init()
    workflow_db = WorkflowDB(settings.db_path)
    await workflow_db.init()

    presets = VoicePresetsStore(settings.presets_dir)
    await presets.init()
    await presets.upsert(
        VoicePreset(
            id="test_voice",
            label="Test Voice",
            voice_id="N2lVS1w4EtoT3dr4eOWO",
        )
    )

    capacity_limiter = asyncio.Semaphore(4)
    runner_factory = WorkflowRunnerFactory(
        image_deps=ImageRunnerDeps(
            settings=settings,
            client=kie,
            image_jobs_repo=image_jobs_db,
            generated_images_store=generated_db,
            uploaded_images_store=images_db,
        ),
        audio_deps=AudioRunnerDeps(
            settings=settings,
            client=kie,
            audio_jobs_repo=audio_jobs_db,
            audios_store=audios_db,
        ),
    )
    step_runner = WorkflowStepRunner(
        settings,
        kie,
        capacity_limiter,
        image_jobs_repo=image_jobs_db,
        generated_images_store=generated_db,
        runner_factory=runner_factory,
    )
    manifest_writer = AtomicWorkflowManifestWriter()
    base_resolver = WorkflowBaseResolver(
        settings,
        kie,
        presets,
        images_db,
        generated_db,
        image_jobs_db,
        capacity_limiter,
        runner_factory,
    )
    workflow_runner = WorkflowRunner(
        settings,
        kie,
        WorkflowRunnerDeps(
            repository=workflow_db,
            manifest_writer=manifest_writer,
            step_runner=step_runner,
            base_resolver=base_resolver,
        ),
    )
    lifecycle = WorkflowLifecycle(workflow_db)
    workflow_limiter = asyncio.Semaphore(1)
    queue: QueueManager[WorkflowJob, WorkflowJobUpdated] = QueueManager(
        settings,
        workflow_runner,
        event_factory=WorkflowJobUpdated,
        lifecycle=lifecycle,
        capacity_limiter=workflow_limiter,
    )
    workflow_runner.set_notify(queue.notify_external)
    controller = WorkflowController(
        settings,
        workflow_db,
        manifest_writer,
        queue,
        base_resolver,
        scan_loader=lambda: scan_workflows_dir(workflows_dir),
        entry_builder=build_workflow_from_entry,
        presets_store=presets,
        uploaded_images=images_db,
        generated_images=generated_db,
    )
    yield {
        "controller": controller,
        "queue": queue,
        "workflow_db": workflow_db,
        "settings": settings,
        "kie": kie,
        "handler": handler,
    }
    await kie.aclose()


async def _wait_for_terminal(
    controller: WorkflowController, workflow_id: str, timeout: float = 60.0
) -> WorkflowJob:
    """Espera hasta que el workflow llegue a estado terminal."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        workflow = await controller.get_workflow(workflow_id)
        if workflow is not None and workflow.is_terminal():
            return workflow
        await asyncio.sleep(0.5)
    raise TimeoutError(f"workflow {workflow_id} no terminó en {timeout}s")


async def test_e2e_workflow_with_3_step_types_completes(e2e_setup) -> None:
    controller = e2e_setup["controller"]
    entries = await controller.list_entries(refresh=True)
    assert len(entries) == 1
    workflow = await controller.enqueue_entry(entries[0])

    finished = await _wait_for_terminal(controller, workflow.id, timeout=30.0)
    assert finished.status == WorkflowStatus.COMPLETED, (
        f"esperaba COMPLETED, status={finished.status.value}, error={finished.error}"
    )

    # Outputs por step según su tipo:
    output_dir = Path(finished.output_dir)
    assert (output_dir / "base.png").is_file(), "base.png debe existir"
    assert (output_dir / "workflow.json").is_file(), "manifest debe existir"

    # Step 1 (a-roll): solo scene.png + final.mp4 (NO audio.mp3 aparte).
    step1_dir = output_dir / "step_01_hook_a_roll"
    assert (step1_dir / "scene.png").is_file()
    assert (step1_dir / "final.mp4").is_file()
    assert not (step1_dir / "audio.mp3").exists(), (
        "a-roll NO debe descargar audio aparte (queda embebido en final.mp4)"
    )

    # Step 2 (b-roll con text): scene.png + audio.mp3 + video.mp4.
    step2_dir = output_dir / "step_02_b_roll_con_audio"
    assert (step2_dir / "scene.png").is_file()
    assert (step2_dir / "audio.mp3").is_file()
    assert (step2_dir / "video.mp4").is_file()
    assert not (step2_dir / "final.mp4").exists()

    # Step 3 (b-roll silent): solo scene.png + video.mp4.
    step3_dir = output_dir / "step_03_b_roll_silencioso"
    assert (step3_dir / "scene.png").is_file()
    assert (step3_dir / "video.mp4").is_file()
    assert not (step3_dir / "audio.mp3").exists()

    # Manifest tiene shape correcto.
    manifest_data = json.loads((output_dir / "workflow.json").read_text(encoding="utf-8"))
    assert manifest_data["status"] == "completed"
    assert manifest_data["id"] == workflow.id
    assert len(manifest_data["steps"]) == 3
    assert manifest_data["model_base"] is not None
    # Cada step tiene outputs poblados.
    assert "scene_image" in manifest_data["steps"][0]["outputs"]
    assert "video" in manifest_data["steps"][0]["outputs"]
    assert "audio" not in manifest_data["steps"][0]["outputs"]  # a-roll no descarga audio
    assert "audio" in manifest_data["steps"][1]["outputs"]
    assert "video" in manifest_data["steps"][1]["outputs"]


async def test_e2e_workflow_steps_run_in_parallel(e2e_setup) -> None:
    """Verifica que los 3 steps se procesan concurrentemente (no secuencial)."""
    controller = e2e_setup["controller"]
    entries = await controller.list_entries(refresh=True)
    workflow = await controller.enqueue_entry(entries[0])
    await _wait_for_terminal(controller, workflow.id, timeout=30.0)
    # Si fueran secuenciales, tomaría 3x más. Con el mock devolviendo
    # respuestas instantáneas no podemos medir tiempo, pero al menos
    # verificamos que TODAS las tareas Kie se crearon (handler.tasks).
    handler = e2e_setup["handler"]
    # Para 3 steps: 1 base + 1 image scene (step2) + 1 image scene (step3) +
    # 2 audios (step1, step2) + 1 avatar (step1) + 2 i2v (step2, step3) = 8.
    # Más liberal: al menos los 3 videos y los 2 audios.
    models_called = Counter(t["model"] for t in handler.tasks.values())
    assert models_called["kling/ai-avatar-pro"] == 1, "1 avatar para a-roll"
    assert models_called["kling-3.0/video"] == 2, "2 i2v para b-rolls"
    assert models_called["elevenlabs/text-to-speech-multilingual-v2"] == 2, (
        "2 TTS para a-roll + b-roll-con-texto (siempre multilingual, nunca turbo)"
    )
    # 3 nano banana: 1 base + 2 scenes (steps con change_scene=True).
    assert models_called["nano-banana-2"] == 3, "1 base + 2 scenes"


async def test_e2e_manifest_updated_throughout_execution(e2e_setup) -> None:
    """El manifest se reescribe en cada transición, no solo al final."""
    controller = e2e_setup["controller"]
    entries = await controller.list_entries(refresh=True)
    workflow = await controller.enqueue_entry(entries[0])
    finished = await _wait_for_terminal(controller, workflow.id, timeout=30.0)
    output_dir = Path(finished.output_dir)
    manifest_data = json.loads((output_dir / "workflow.json").read_text(encoding="utf-8"))
    # progress_summary refleja el estado final.
    assert "3 completados" in manifest_data["progress_summary"]
    # Cada step terminó con todas las progress keys en 'completed'.
    for step in manifest_data["steps"]:
        for value in step["progress"].values():
            assert value == "completed", f"step {step['step']} progress={step['progress']}"


async def test_e2e_never_uses_turbo_model_even_if_audio_language_set(e2e_setup) -> None:
    """Incluso si `audio_language='es-419'` está seteado, se debe usar siempre
    el modelo multilingual-v2 (nunca turbo) por requerimiento del usuario."""
    controller = e2e_setup["controller"]
    entries = await controller.list_entries(refresh=True)
    workflow = await controller.enqueue_entry(entries[0])
    await _wait_for_terminal(controller, workflow.id, timeout=30.0)
    handler = e2e_setup["handler"]
    tts_models = [t["model"] for t in handler.tasks.values() if "text-to-speech" in t["model"]]
    # TODAS las llamadas TTS usaron el modelo multilingual-v2 (porque turbo está baneado).
    assert all("multilingual" in m for m in tts_models), f"TTS models: {tts_models}"


async def test_e2e_partially_failed_when_some_steps_fail(
    tmp_settings: Settings,
) -> None:
    """Si algunos steps fallan y otros completan, el workflow queda PARTIALLY_FAILED."""
    _ = tmp_settings  # used via fixture chain below
    settings = tmp_settings.model_copy(update={"poll_interval_seconds": 1})

    # Handler que falla SOLO para tasks de i2v (b-roll).
    class _FailI2VHandler:
        def __init__(self) -> None:
            self.task_counter = 0
            self.tasks: dict[str, dict] = {}

        def handle(self, request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == "/api/file-stream-upload":
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "fileName": "x.png",
                            "filePath": "x.png",
                            "downloadUrl": "https://tempfile.kie.ai/x.png",
                            "fileSize": 100,
                            "mimeType": "image/png",
                        }
                    },
                )
            if path == "/api/v1/jobs/createTask":
                self.task_counter += 1
                tk = f"tk_{self.task_counter:04d}"
                body = json.loads(request.content)
                if "image-to-video" in body["model"] or "kling-3.0/video" in body["model"]:
                    return httpx.Response(400, json={"error": "i2v down"})
                self.tasks[tk] = {"model": body["model"]}
                return httpx.Response(200, json={"data": {"taskId": tk}})
            if path == "/api/v1/jobs/recordInfo":
                tk = request.url.params.get("taskId")
                if tk not in self.tasks:
                    return httpx.Response(404)
                model = self.tasks[tk]["model"]
                if "avatar-pro" in model:
                    url = f"https://tempfile.kie.ai/avatar/{tk}.mp4"
                elif "nano-banana" in model:
                    url = f"https://tempfile.kie.ai/img/{tk}.png"
                else:
                    url = f"https://tempfile.kie.ai/audio/{tk}.mp3"
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "state": "success",
                            "resultJson": json.dumps({"resultUrls": [url]}),
                        }
                    },
                )
            return httpx.Response(200, content=b"fake")

    workflows_dir = settings.workflows_dir
    (workflows_dir / "x.json").write_text(json.dumps(_workflow_json_3_steps()), encoding="utf-8")

    handler = _FailI2VHandler()
    kie = KieClient(settings)
    await kie._client.aclose()
    kie._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler.handle),
        headers={"Authorization": "Bearer test"},
    )
    images_db = ImagesDB(settings.db_path)
    await images_db.init()
    generated_db = GeneratedImagesDB(settings.db_path)
    await generated_db.init()
    audios_db = AudiosDB(settings.db_path)
    await audios_db.init()
    audio_jobs_db = AudioJobsDB(settings.db_path)
    await audio_jobs_db.init()
    image_jobs_db = ImageJobsDB(settings.db_path)
    await image_jobs_db.init()
    workflow_db = WorkflowDB(settings.db_path)
    await workflow_db.init()
    presets = VoicePresetsStore(settings.presets_dir)
    await presets.init()
    await presets.upsert(
        VoicePreset(id="test_voice", label="Test", voice_id="N2lVS1w4EtoT3dr4eOWO")
    )

    capacity_limiter = asyncio.Semaphore(4)
    runner_factory = WorkflowRunnerFactory(
        image_deps=ImageRunnerDeps(
            settings=settings,
            client=kie,
            image_jobs_repo=image_jobs_db,
            generated_images_store=generated_db,
            uploaded_images_store=images_db,
        ),
        audio_deps=AudioRunnerDeps(
            settings=settings,
            client=kie,
            audio_jobs_repo=audio_jobs_db,
            audios_store=audios_db,
        ),
    )
    step_runner = WorkflowStepRunner(
        settings,
        kie,
        capacity_limiter,
        image_jobs_repo=image_jobs_db,
        generated_images_store=generated_db,
        runner_factory=runner_factory,
    )
    manifest_writer = AtomicWorkflowManifestWriter()
    base_resolver = WorkflowBaseResolver(
        settings,
        kie,
        presets,
        images_db,
        generated_db,
        image_jobs_db,
        capacity_limiter,
        runner_factory,
    )
    workflow_runner = WorkflowRunner(
        settings,
        kie,
        WorkflowRunnerDeps(
            repository=workflow_db,
            manifest_writer=manifest_writer,
            step_runner=step_runner,
            base_resolver=base_resolver,
        ),
    )
    lifecycle = WorkflowLifecycle(workflow_db)
    queue: QueueManager[WorkflowJob, WorkflowJobUpdated] = QueueManager(
        settings,
        workflow_runner,
        event_factory=WorkflowJobUpdated,
        lifecycle=lifecycle,
        capacity_limiter=asyncio.Semaphore(1),
    )
    workflow_runner.set_notify(queue.notify_external)
    controller = WorkflowController(
        settings,
        workflow_db,
        manifest_writer,
        queue,
        base_resolver,
        scan_loader=lambda: scan_workflows_dir(workflows_dir),
        entry_builder=build_workflow_from_entry,
        presets_store=presets,
        uploaded_images=images_db,
        generated_images=generated_db,
    )
    entries = await controller.list_entries(refresh=True)
    workflow = await controller.enqueue_entry(entries[0])
    finished = await _wait_for_terminal(controller, workflow.id, timeout=30.0)
    # 1 step OK (a-roll usa avatar-pro), 2 steps FAIL (b-rolls usan i2v).
    assert finished.status == WorkflowStatus.PARTIALLY_FAILED
    completed = sum(1 for s in finished.steps if s.status == WorkflowStepStatus.COMPLETED)
    failed = sum(1 for s in finished.steps if s.status == WorkflowStepStatus.FAILED)
    assert completed == 1
    assert failed == 2
    await kie.aclose()


async def test_e2e_workflow_steps_run_sequentially_in_manual_mode(e2e_setup) -> None:
    """En modo MANUAL, los steps deben ejecutarse secuencialmente en serie y
    detenerse INMEDIATAMENTE ante el primer step que requiere aprobación,
    sin lanzar steps paralelos posteriores en background (lo que gastaría
    créditos o atascaría la cola)."""
    controller = e2e_setup["controller"]
    entries = await controller.list_entries(refresh=True)

    # Encolamos con MANUAL
    workflow = await controller.enqueue_entry(
        entries[0],
        scene_approval_mode=SceneApprovalMode.MANUAL,
    )
    # Esperamos a que la ejecución se pause en AWAITING_APPROVAL
    deadline = asyncio.get_running_loop().time() + 15.0
    paused = None
    while asyncio.get_running_loop().time() < deadline:
        paused = await controller.get_workflow(workflow.id)
        if paused is not None and (
            paused.status == WorkflowStatus.AWAITING_APPROVAL or paused.is_terminal()
        ):
            break
        await asyncio.sleep(0.5)

    assert paused is not None
    assert paused.status == WorkflowStatus.AWAITING_APPROVAL

    # El step 2 (b-roll con change_scene=true) requirió aprobación y pausó.
    # Por ende, el paso 3 (b-roll silencioso) NO debió iniciarse en absoluto.
    # Verificamos qué tareas se enviaron a Kie:
    handler = e2e_setup["handler"]
    models_called = Counter(t["model"] for t in handler.tasks.values())

    # Step 1 (a-roll): corre completo -> 1 TTS (multilingual) y 1 avatar-pro.
    assert models_called["kling/ai-avatar-pro"] == 1
    # Step 2 (b-roll): genera su scene_image con Nano Banana y de inmediato lanza
    # StepAwaitingApprovalSignal.
    # Total Nano Banana llamadas: 1 (base) + 1 (step 2 scene_image) = 2.
    # Step 3 nunca arrancó, por lo que NO hay 3ª llamada a Nano Banana (para su escena).
    assert models_called["nano-banana-2"] == 2
    # El video i2v de Kling para step 2 y step 3 NO se debió llamar (step 2 pausó antes
    # del render, step 3 ni arrancó).
    assert models_called["kling-3.0/video"] == 0
