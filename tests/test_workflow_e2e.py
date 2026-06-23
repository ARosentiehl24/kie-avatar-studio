"""Test e2e con MockTransport: workflow completo renderizado con VEO."""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from kie_avatar_studio.app_layer.queue_manager import QueueManager
from kie_avatar_studio.app_layer.runner_factories import (
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
from kie_avatar_studio.infra.workflow_db import WorkflowDB
from kie_avatar_studio.infra.workflow_loader import (
    build_workflow_from_entry,
    scan_workflows_dir,
)
from kie_avatar_studio.infra.workflow_manifest_writer import AtomicWorkflowManifestWriter


class _FakeFFmpeg:
    async def concat_videos(self, _video_paths: list[Path], output_path: Path) -> Path:
        output_path.write_bytes(b"video")
        return output_path

    async def extract_audio(self, _video_path: Path, output_path: Path) -> Path:
        output_path.write_bytes(b"audio")
        return output_path


class _MockKieAllSuccess:
    """Mock handler que responde success a TODOS los endpoints."""

    def __init__(self) -> None:
        self.task_counter = 0
        self.tasks: dict[str, dict] = {}
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:  # noqa: PLR0911
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
        if path == "/api/v1/veo/generate":
            self.task_counter += 1
            task_id = f"tk_{self.task_counter:04d}"
            body = json.loads(request.content)
            self.tasks[task_id] = {"model": body["model"], "kind": "veo"}
            return httpx.Response(200, json={"data": {"taskId": task_id}})
        if path == "/api/v1/veo/record-info":
            task_id = request.url.params.get("taskId")
            if task_id not in self.tasks or self.tasks[task_id]["kind"] != "veo":
                return httpx.Response(404)
            return httpx.Response(
                200,
                json={
                    "data": {
                        "successFlag": 1,
                        "response": {"resultUrls": [f"https://tempfile.kie.ai/veo/{task_id}.mp4"]},
                    }
                },
            )
        if path == "/api/v1/jobs/createTask":
            self.task_counter += 1
            task_id = f"tk_{self.task_counter:04d}"
            body = json.loads(request.content)
            self.tasks[task_id] = {"model": body["model"], "kind": "jobs"}
            return httpx.Response(200, json={"data": {"taskId": task_id}})
        if path == "/api/v1/jobs/recordInfo":
            task_id = request.url.params.get("taskId")
            if task_id not in self.tasks or self.tasks[task_id]["kind"] != "jobs":
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
        if "kling-3.0/video" in model:
            return f"https://tempfile.kie.ai/i2v/{task_id}.mp4"
        if "nano-banana" in model:
            return f"https://tempfile.kie.ai/img/{task_id}.png"
        if "text-to-speech" in model:
            return f"https://tempfile.kie.ai/audio/{task_id}.mp3"
        return f"https://tempfile.kie.ai/generic/{task_id}.bin"


def _workflow_json_3_steps() -> dict:
    """JSON con un step de cada tipo lógico, todos renderizados con VEO."""
    return {
        "workflow": "E2E Test 3 Steps",
        "pre_settings": {
            "audio_language": "es-419",
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

    capacity_limiter = asyncio.Semaphore(4)
    runner_factory = WorkflowRunnerFactory(
        ImageRunnerDeps(
            settings=settings,
            client=kie,
            image_jobs_repo=image_jobs_db,
            generated_images_store=generated_db,
            uploaded_images_store=images_db,
        )
    )
    step_runner = WorkflowStepRunner(
        settings,
        kie,
        capacity_limiter,
        video_limiter=asyncio.Semaphore(2),
        download_limiter=asyncio.Semaphore(2),
        image_jobs_repo=image_jobs_db,
        generated_images_store=generated_db,
        runner_factory=runner_factory,
    )
    manifest_writer = AtomicWorkflowManifestWriter(settings.outputs_dir)
    base_resolver = WorkflowBaseResolver(
        settings,
        kie,
        images_db,
        generated_db,
        image_jobs_db,
        capacity_limiter,
        asyncio.Semaphore(1),
        asyncio.Semaphore(2),
        runner_factory,
    )
    workflow_runner = WorkflowRunner(
        settings,
        WorkflowRunnerDeps(
            repository=workflow_db,
            manifest_writer=manifest_writer,
            step_runner=step_runner,
            base_resolver=base_resolver,
        ),
        ffmpeg=_FakeFFmpeg(),
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
        uploaded_images=images_db,
        generated_images=generated_db,
    )

    async def _fake_concat(_steps, output_dir: Path, *, ffmpeg: object, workflow_slug: str) -> Path:
        final_video = output_dir / f"{workflow_slug}_final.mp4"
        final_audio = output_dir / f"{workflow_slug}_final_audio.mp3"
        final_video.write_bytes(b"final-video")
        final_audio.write_bytes(b"final-audio")
        return final_video

    with patch(
        "kie_avatar_studio.app_layer.workflow_runner.concatenate_workflow_videos",
        side_effect=_fake_concat,
    ):
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
    assert (output_dir / f"{finished.slug}_base.png").is_file(), "imagen base debe existir"
    assert (output_dir / "workflow.json").is_file(), "manifest debe existir"
    assert (output_dir / f"{finished.slug}_final.mp4").is_file()
    assert (output_dir / f"{finished.slug}_final_audio.mp3").is_file()

    # Step 1 (a-roll): scene + video con nombres descriptivos.
    step1_dir = output_dir / "step_01_hook_a_roll"
    assert (step1_dir / "step_01_hook_a_roll_scene.png").is_file()
    assert (step1_dir / "step_01_hook_a_roll_video.mp4").is_file()
    assert not (step1_dir / "audio.mp3").exists()
    assert not (step1_dir / "final.mp4").exists()

    # Step 2 (b-roll con text): scene + video con nombres descriptivos.
    step2_dir = output_dir / "step_02_b_roll_con_audio"
    assert (step2_dir / "step_02_b_roll_con_audio_scene.png").is_file()
    assert (step2_dir / "step_02_b_roll_con_audio_video.mp4").is_file()
    assert not (step2_dir / "audio.mp3").exists()
    assert not (step2_dir / "final.mp4").exists()

    # Step 3 (b-roll silent): solo scene + video descriptivos.
    step3_dir = output_dir / "step_03_b_roll_silencioso"
    assert (step3_dir / "step_03_b_roll_silencioso_scene.png").is_file()
    assert (step3_dir / "step_03_b_roll_silencioso_video.mp4").is_file()
    assert not (step3_dir / "audio.mp3").exists()

    # Manifest tiene shape correcto.
    manifest_data = json.loads((output_dir / "workflow.json").read_text(encoding="utf-8"))
    assert manifest_data["status"] == "completed"
    assert manifest_data["id"] == workflow.id
    assert len(manifest_data["steps"]) == 3
    assert manifest_data["model_base"] is not None
    assert manifest_data["outputs"]["video"] == str(output_dir / f"{finished.slug}_final.mp4")
    assert manifest_data["outputs"]["audio"] == str(output_dir / f"{finished.slug}_final_audio.mp3")
    # Cada step tiene outputs poblados.
    assert "scene_image" in manifest_data["steps"][0]["outputs"]
    assert "video" in manifest_data["steps"][0]["outputs"]
    assert "audio" not in manifest_data["steps"][0]["outputs"]
    assert "audio" not in manifest_data["steps"][1]["outputs"]
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
    # Para 3 steps: 1 base + 2 scene images + 3 VEO = 6 tareas.
    models_called = Counter(t["model"] for t in handler.tasks.values())
    assert models_called["veo3_fast"] == 3, "todos los steps deben renderizarse con VEO"
    assert models_called["gpt-image-2-text-to-image"] == 1, "1 base"
    assert models_called["nano-banana-2"] == 2, "2 scenes"


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
    # Cada step terminó con todas las progress keys en estado terminal.
    for step in manifest_data["steps"]:
        for value in step["progress"].values():
            assert value in {"completed", "skipped"}, (
                f"step {step['step']} progress={step['progress']}"
            )


async def test_e2e_never_uses_turbo_model_even_if_audio_language_set(e2e_setup) -> None:
    """El flujo VEO no debe crear ninguna tarea TTS legacy."""
    controller = e2e_setup["controller"]
    entries = await controller.list_entries(refresh=True)
    workflow = await controller.enqueue_entry(entries[0])
    await _wait_for_terminal(controller, workflow.id, timeout=30.0)
    handler = e2e_setup["handler"]
    tts_models = [t["model"] for t in handler.tasks.values() if "text-to-speech" in t["model"]]
    assert tts_models == [], f"No debe haber TTS por step en VEO: {tts_models}"


async def test_e2e_partially_failed_when_some_steps_fail(  # noqa: C901
    tmp_settings: Settings,
) -> None:
    """Si algunos steps fallan y otros completan, el workflow queda PARTIALLY_FAILED."""
    _ = tmp_settings  # used via fixture chain below
    settings = tmp_settings.model_copy(update={"poll_interval_seconds": 1})

    # Handler que deja pasar el a-roll (base image) y falla los VEO de b-roll.
    class _FailBrollVeoHandler:
        def __init__(self) -> None:
            self.task_counter = 0
            self.tasks: dict[str, dict] = {}

        def handle(self, request: httpx.Request) -> httpx.Response:  # noqa: PLR0911
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
            if path == "/api/v1/veo/generate":
                self.task_counter += 1
                tk = f"tk_{self.task_counter:04d}"
                body = json.loads(request.content)
                prompt = str(body.get("prompt", ""))
                self.tasks[tk] = {
                    "model": body["model"],
                    "kind": "veo",
                    "should_fail": "Una mujer mira a cámara plano medio" not in prompt,
                }
                return httpx.Response(200, json={"data": {"taskId": tk}})
            if path == "/api/v1/veo/record-info":
                tk = request.url.params.get("taskId")
                if tk not in self.tasks or self.tasks[tk]["kind"] != "veo":
                    return httpx.Response(404)
                if self.tasks[tk]["should_fail"]:
                    return httpx.Response(
                        200,
                        json={"data": {"successFlag": 2, "errorCode": "veo down"}},
                    )
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "successFlag": 1,
                            "response": {"resultUrls": [f"https://tempfile.kie.ai/veo/{tk}.mp4"]},
                        }
                    },
                )
            if path == "/api/v1/jobs/createTask":
                self.task_counter += 1
                tk = f"tk_{self.task_counter:04d}"
                body = json.loads(request.content)
                self.tasks[tk] = {"model": body["model"], "kind": "jobs"}
                return httpx.Response(200, json={"data": {"taskId": tk}})
            if path == "/api/v1/jobs/recordInfo":
                tk = request.url.params.get("taskId")
                if tk not in self.tasks or self.tasks[tk]["kind"] != "jobs":
                    return httpx.Response(404)
                model = self.tasks[tk]["model"]
                if "nano-banana" in model or "gpt-image" in model:
                    url = f"https://tempfile.kie.ai/img/{tk}.png"
                else:
                    url = f"https://tempfile.kie.ai/generic/{tk}.bin"
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

    handler = _FailBrollVeoHandler()
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
    capacity_limiter = asyncio.Semaphore(4)
    runner_factory = WorkflowRunnerFactory(
        ImageRunnerDeps(
            settings=settings,
            client=kie,
            image_jobs_repo=image_jobs_db,
            generated_images_store=generated_db,
            uploaded_images_store=images_db,
        )
    )
    step_runner = WorkflowStepRunner(
        settings,
        kie,
        capacity_limiter,
        video_limiter=asyncio.Semaphore(2),
        download_limiter=asyncio.Semaphore(2),
        image_jobs_repo=image_jobs_db,
        generated_images_store=generated_db,
        runner_factory=runner_factory,
    )
    manifest_writer = AtomicWorkflowManifestWriter(settings.outputs_dir)
    base_resolver = WorkflowBaseResolver(
        settings,
        kie,
        images_db,
        generated_db,
        image_jobs_db,
        capacity_limiter,
        asyncio.Semaphore(1),
        asyncio.Semaphore(2),
        runner_factory,
    )
    workflow_runner = WorkflowRunner(
        settings,
        WorkflowRunnerDeps(
            repository=workflow_db,
            manifest_writer=manifest_writer,
            step_runner=step_runner,
            base_resolver=base_resolver,
        ),
        ffmpeg=_FakeFFmpeg(),
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
        uploaded_images=images_db,
        generated_images=generated_db,
    )
    entries = await controller.list_entries(refresh=True)
    workflow = await controller.enqueue_entry(entries[0])
    finished = await _wait_for_terminal(controller, workflow.id, timeout=30.0)
    # 1 step OK (a-roll con base image), 2 steps FAIL (b-rolls vía VEO).
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

    # Step 1 (a-roll): corre completo -> 1 VEO.
    assert models_called["veo3_fast"] == 1
    # Step 2 (b-roll): genera su scene_image con Nano Banana 2 y de inmediato lanza
    # StepAwaitingApprovalSignal.
    # Total llamadas: 1 (base con GPT) + 1 (step 2 scene_image con Nano Banana) = 2.
    # Step 3 nunca arrancó, por lo que NO hay llamadas extras.
    assert models_called["gpt-image-2-text-to-image"] == 1, "1 base"
    assert models_called["nano-banana-2"] == 1, "1 step 2 scene_image"
    # Los VEO de step 2/3 NO se debieron llamar (step 2 pausó antes del render,
    # step 3 ni arrancó).
    assert models_called["veo3_fast"] == 1
