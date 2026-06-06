"""Tests del `WorkflowStepRunner` con los 3 paths (a-roll / b-roll / b-roll silent)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from kie_avatar_studio.app_layer.runner_factories import (
    AudioRunnerDeps,
    ImageRunnerDeps,
    WorkflowRunnerFactory,
)
from kie_avatar_studio.app_layer.workflow_execution_context import (
    DEFAULT_TURBO_MODEL,
    WorkflowExecutionContext,
)
from kie_avatar_studio.app_layer.workflow_step_runner import (
    A_ROLL_VIDEO_FILENAME,
    AUDIO_FILENAME,
    B_ROLL_VIDEO_FILENAME,
    SCENE_IMAGE_FILENAME,
    WorkflowStepRunner,
)
from kie_avatar_studio.config import Settings
from kie_avatar_studio.domain.models import (
    ImageAssetKind,
    ImageAssetRef,
    StepType,
    VoiceSettings,
    WorkflowProgressKey,
    WorkflowProgressStatus,
    WorkflowStep,
    WorkflowStepStatus,
)
from kie_avatar_studio.infra.audio_jobs_db import AudioJobsDB
from kie_avatar_studio.infra.audios_db import AudiosDB
from kie_avatar_studio.infra.generated_images_db import GeneratedImagesDB
from kie_avatar_studio.infra.image_jobs_db import ImageJobsDB
from kie_avatar_studio.infra.images_db import ImagesDB
from kie_avatar_studio.infra.kie_client import KieClient


class _MockKieHandler:
    """Handler programable que simula respuestas de Kie para flujos end-to-end.

    Devuelve task_ids monotonicos, status='success', y URLs predecibles
    para que los tests verifiquen el flujo sin red real.
    """

    def __init__(self) -> None:
        self.task_counter = 0
        self.tasks: dict[str, dict] = {}
        self.uploaded_files: list[Path] = []
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/api/file-stream-upload":
            return self._handle_upload(request)
        if path == "/api/v1/jobs/createTask":
            return self._handle_create_task(request)
        if path == "/api/v1/jobs/recordInfo":
            return self._handle_record_info(request)
        # Descargas: cualquier URL fuera del API base se simula como binary.
        if request.url.host.endswith("kie.ai") or "tempfile" in request.url.host:
            return httpx.Response(200, content=b"fake binary content")
        return httpx.Response(404)

    def _handle_upload(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "fileName": "modelo.png",
                    "filePath": "uploads/modelo.png",
                    "downloadUrl": "https://tempfile.kie.ai/uploads/modelo.png",
                    "fileSize": 123456,
                    "mimeType": "image/png",
                }
            },
        )

    def _handle_create_task(self, request: httpx.Request) -> httpx.Response:
        self.task_counter += 1
        task_id = f"tk_{self.task_counter:04d}"
        body = json.loads(request.content)
        self.tasks[task_id] = {"model": body["model"], "input": body.get("input", {})}
        return httpx.Response(200, json={"data": {"taskId": task_id}})

    def _handle_record_info(self, request: httpx.Request) -> httpx.Response:
        task_id = request.url.params.get("taskId")
        if task_id is None or task_id not in self.tasks:
            return httpx.Response(404, json={"error": "not found"})
        model = self.tasks[task_id]["model"]
        result_url = self._result_url_for_model(model, task_id)
        return httpx.Response(
            200,
            json={
                "data": {
                    "state": "success",
                    "resultJson": json.dumps({"resultUrls": [result_url]}),
                }
            },
        )

    @staticmethod
    def _result_url_for_model(model: str, task_id: str) -> str:
        if "kling/ai-avatar-pro" in model:
            return f"https://tempfile.kie.ai/avatar/{task_id}.mp4"
        if "image-to-video" in model:
            return f"https://tempfile.kie.ai/i2v/{task_id}.mp4"
        if "nano-banana" in model:
            return f"https://tempfile.kie.ai/img/{task_id}.png"
        if "text-to-speech" in model:
            return f"https://tempfile.kie.ai/audio/{task_id}.mp3"
        return f"https://tempfile.kie.ai/generic/{task_id}.bin"


@pytest.fixture
def mock_handler() -> _MockKieHandler:
    return _MockKieHandler()


@pytest.fixture
async def kie_with_handler(
    tmp_settings: Settings, mock_handler: _MockKieHandler
) -> KieClient:
    settings = tmp_settings.model_copy(update={"poll_interval_seconds": 1})
    client = KieClient(settings)
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(mock_handler.handle),
        headers={"Authorization": "Bearer test"},
    )
    yield client
    await client.aclose()


@pytest.fixture
async def step_runner_setup(
    tmp_settings: Settings, kie_with_handler: KieClient
) -> tuple[WorkflowStepRunner, asyncio.Semaphore, Path]:
    images_db = ImagesDB(tmp_settings.db_path)
    await images_db.init()
    audios_db = AudiosDB(tmp_settings.db_path)
    await audios_db.init()
    audio_jobs = AudioJobsDB(tmp_settings.db_path)
    await audio_jobs.init()
    image_jobs = ImageJobsDB(tmp_settings.db_path)
    await image_jobs.init()
    generated = GeneratedImagesDB(tmp_settings.db_path)
    await generated.init()
    # Persistimos la imagen base mock para que `ImageJobRunner` la pueda
    # revalidar al usarla como ref en `_generate_scene_image`.
    from kie_avatar_studio.domain.models import GeneratedImage

    await generated.upsert(
        GeneratedImage(
            id="img_base",
            label="base",
            prompt="base prompt",
            kie_url="https://tempfile.kie.ai/base.png",
            kie_file_path="base.png",
        )
    )
    limiter = asyncio.Semaphore(2)
    runner_factory = WorkflowRunnerFactory(
        image_deps=ImageRunnerDeps(
            settings=tmp_settings,
            client=kie_with_handler,
            image_jobs_repo=image_jobs,
            generated_images_store=generated,
            uploaded_images_store=images_db,
        ),
        audio_deps=AudioRunnerDeps(
            settings=tmp_settings,
            client=kie_with_handler,
            audio_jobs_repo=audio_jobs,
            audios_store=audios_db,
        ),
    )
    runner = WorkflowStepRunner(
        tmp_settings,
        kie_with_handler,
        limiter,
        image_jobs_repo=image_jobs,
        generated_images_store=generated,
        runner_factory=runner_factory,
    )
    return runner, limiter, tmp_settings.outputs_dir / "wf_test_001"


def _make_context(output_dir: Path) -> WorkflowExecutionContext:
    return WorkflowExecutionContext(
        audio_language="es-419",
        voice_id="pNInz6obpgDQGcFmaJgB",
        voice_settings=None,
        base_image_ref=ImageAssetRef(
            kind=ImageAssetKind.GENERATED,
            id="img_base",
            label="base",
            kie_url="https://tempfile.kie.ai/base.png",
            expires_at=datetime.now(UTC) + timedelta(days=14),
        ),
        output_dir=output_dir,
    )


def _a_roll_step() -> WorkflowStep:
    return WorkflowStep(
        step=1,
        scene_name="Hook 1",
        scene_slug="hook_1",
        type=StepType.A_ROLL,
        change_background=False,
        prompt="Una persona mira a cámara",
        text="Hola mundo, esto es un test.",
    )


def _b_roll_with_text_step() -> WorkflowStep:
    return WorkflowStep(
        step=2,
        scene_name="Pain B Roll",
        scene_slug="pain_b_roll",
        type=StepType.B_ROLL,
        change_background=True,
        background_description="Close-up de jeans",
        prompt="Hands struggling to button jeans",
        text="Esta es una narración para el b-roll",
    )


def _b_roll_silent_step() -> WorkflowStep:
    return WorkflowStep(
        step=3,
        scene_name="Product Reveal",
        scene_slug="product_reveal",
        type=StepType.B_ROLL,
        change_background=True,
        background_description="Apothecary jar on linen",
        prompt="Beautifully lit close-up of an amber jar",
        text="",
    )


async def test_a_roll_path_creates_final_mp4_without_separate_audio(
    step_runner_setup: tuple[WorkflowStepRunner, asyncio.Semaphore, Path]
) -> None:
    runner, _limiter, output_dir = step_runner_setup
    step = _a_roll_step()
    transitions: list[WorkflowStep] = []

    async def on_transition(s: WorkflowStep) -> None:
        transitions.append(s.model_copy(deep=True))

    result = await runner.run(step, _make_context(output_dir), on_transition)
    assert result.status == WorkflowStepStatus.COMPLETED
    assert result.video_path is not None
    assert result.video_path.endswith(A_ROLL_VIDEO_FILENAME)
    assert result.scene_image_path is not None
    # CRÍTICO: a-roll NO descarga audio aparte (queda embebido en final.mp4).
    assert result.audio_path is None
    # Pero el AudioJob SÍ se creó y persistió (visible en pantalla Audios).
    assert result.audio_job_id is not None
    # Final mp4 existe en filesystem.
    assert Path(result.video_path).is_file()
    # Scene image existe en filesystem.
    assert Path(result.scene_image_path).is_file()
    # Progress completo.
    assert result.progress[WorkflowProgressKey.SCENE_IMAGE] == WorkflowProgressStatus.COMPLETED
    assert result.progress[WorkflowProgressKey.AUDIO] == WorkflowProgressStatus.COMPLETED
    assert result.progress[WorkflowProgressKey.VIDEO] == WorkflowProgressStatus.COMPLETED
    assert result.progress[WorkflowProgressKey.DOWNLOAD] == WorkflowProgressStatus.COMPLETED
    # NO debe tener keys de b-roll.
    assert WorkflowProgressKey.DOWNLOAD_VIDEO not in result.progress
    assert WorkflowProgressKey.DOWNLOAD_AUDIO not in result.progress


async def test_b_roll_with_text_downloads_video_and_audio_separately(
    step_runner_setup: tuple[WorkflowStepRunner, asyncio.Semaphore, Path]
) -> None:
    runner, _limiter, output_dir = step_runner_setup
    step = _b_roll_with_text_step()
    transitions: list[WorkflowStep] = []

    async def on_transition(s: WorkflowStep) -> None:
        transitions.append(s.model_copy(deep=True))

    result = await runner.run(step, _make_context(output_dir), on_transition)
    assert result.status == WorkflowStepStatus.COMPLETED
    assert result.video_path is not None
    assert result.video_path.endswith(B_ROLL_VIDEO_FILENAME)
    # B-roll con text: SÍ descarga audio aparte.
    assert result.audio_path is not None
    assert result.audio_path.endswith(AUDIO_FILENAME)
    assert result.scene_image_path is not None
    # Filesystem: 3 archivos en el step dir.
    step_dir = Path(result.video_path).parent
    assert (step_dir / SCENE_IMAGE_FILENAME).is_file()
    assert (step_dir / AUDIO_FILENAME).is_file()
    assert (step_dir / B_ROLL_VIDEO_FILENAME).is_file()
    # Progress keys de b-roll-con-text.
    assert result.progress[WorkflowProgressKey.DOWNLOAD_VIDEO] == WorkflowProgressStatus.COMPLETED
    assert result.progress[WorkflowProgressKey.DOWNLOAD_AUDIO] == WorkflowProgressStatus.COMPLETED


async def test_b_roll_silent_only_creates_video_no_audio(
    step_runner_setup: tuple[WorkflowStepRunner, asyncio.Semaphore, Path]
) -> None:
    runner, _limiter, output_dir = step_runner_setup
    step = _b_roll_silent_step()
    transitions: list[WorkflowStep] = []

    async def on_transition(s: WorkflowStep) -> None:
        transitions.append(s.model_copy(deep=True))

    result = await runner.run(step, _make_context(output_dir), on_transition)
    assert result.status == WorkflowStepStatus.COMPLETED
    assert result.video_path is not None
    assert result.audio_path is None
    assert result.audio_job_id is None  # NO se creó audio job
    assert result.scene_image_path is not None
    # Progress: solo scene_image + video + download (sin audio key).
    assert WorkflowProgressKey.AUDIO not in result.progress


async def test_change_background_false_reuses_base_image(
    step_runner_setup: tuple[WorkflowStepRunner, asyncio.Semaphore, Path]
) -> None:
    runner, _limiter, output_dir = step_runner_setup
    step = WorkflowStep(
        step=1,
        scene_name="Pure base",
        scene_slug="pure_base",
        type=StepType.A_ROLL,
        change_background=False,
        prompt="Plain shot",
        text="Texto a-roll",
    )

    async def on_transition(_s: WorkflowStep) -> None:
        pass

    result = await runner.run(step, _make_context(output_dir), on_transition)
    assert result.status == WorkflowStepStatus.COMPLETED
    # No se generó imagen scene aparte: se reusó la base.
    # (bg_image_job_id sigue None porque no se creó ImageJob)
    assert result.bg_image_job_id is None
    assert result.scene_image_path is not None


async def test_change_background_true_creates_image_job(
    step_runner_setup: tuple[WorkflowStepRunner, asyncio.Semaphore, Path]
) -> None:
    runner, _limiter, output_dir = step_runner_setup
    step = _b_roll_with_text_step()  # change_background=True

    async def on_transition(_s: WorkflowStep) -> None:
        pass

    result = await runner.run(step, _make_context(output_dir), on_transition)
    assert result.bg_image_job_id is not None  # Sí se creó ImageJob para scene_image.


async def test_transitions_callback_invoked_multiple_times(
    step_runner_setup: tuple[WorkflowStepRunner, asyncio.Semaphore, Path]
) -> None:
    runner, _limiter, output_dir = step_runner_setup
    step = _a_roll_step()
    transitions: list[WorkflowStepStatus] = []

    async def on_transition(s: WorkflowStep) -> None:
        transitions.append(s.status)

    await runner.run(step, _make_context(output_dir), on_transition)
    # Al menos: PREPARING, RENDERING, DOWNLOADING, COMPLETED.
    assert WorkflowStepStatus.PREPARING in transitions
    assert WorkflowStepStatus.RENDERING in transitions
    assert WorkflowStepStatus.DOWNLOADING in transitions
    assert WorkflowStepStatus.COMPLETED in transitions


async def test_failed_step_marks_remaining_progress_as_failed(
    tmp_settings: Settings
) -> None:
    """Si el step falla a mitad, las keys de progress en RUNNING/PENDING
    se marcan FAILED para reflejar el estado real."""
    settings = tmp_settings
    # KieClient que SIEMPRE devuelve 400 para createTask.
    client = KieClient(settings)
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(400, json={"error": "fake"})),
        headers={"Authorization": "Bearer test"},
    )
    images_db = ImagesDB(settings.db_path)
    await images_db.init()
    audios_db = AudiosDB(settings.db_path)
    await audios_db.init()
    audio_jobs = AudioJobsDB(settings.db_path)
    await audio_jobs.init()
    image_jobs = ImageJobsDB(settings.db_path)
    await image_jobs.init()
    generated = GeneratedImagesDB(settings.db_path)
    await generated.init()
    runner_factory = WorkflowRunnerFactory(
        image_deps=ImageRunnerDeps(
            settings=settings,
            client=client,
            image_jobs_repo=image_jobs,
            generated_images_store=generated,
            uploaded_images_store=images_db,
        ),
        audio_deps=AudioRunnerDeps(
            settings=settings,
            client=client,
            audio_jobs_repo=audio_jobs,
            audios_store=audios_db,
        ),
    )
    runner = WorkflowStepRunner(
        settings,
        client,
        asyncio.Semaphore(2),
        image_jobs_repo=image_jobs,
        generated_images_store=generated,
        runner_factory=runner_factory,
    )
    step = _b_roll_with_text_step()
    context = _make_context(settings.outputs_dir / "wf_fail")

    async def on_transition(_s: WorkflowStep) -> None:
        pass

    result = await runner.run(step, context, on_transition)
    assert result.status == WorkflowStepStatus.FAILED
    assert result.error is not None
    # Las keys que estaban en PENDING/RUNNING se marcaron FAILED.
    failed_count = sum(
        1 for v in result.progress.values() if v == WorkflowProgressStatus.FAILED
    )
    assert failed_count >= 1
    await client.aclose()


async def test_resolved_voice_settings_injects_language_code() -> None:
    """Si `audio_language` está seteado, se propaga a `voice_settings.language_code`."""
    ctx = WorkflowExecutionContext(
        audio_language="es-419",
        voice_id="voice_x",
        voice_settings=VoiceSettings(stability=0.5),
        base_image_ref=ImageAssetRef(
            kind=ImageAssetKind.GENERATED,
            id="x",
            label="x",
            kie_url="https://x",
            expires_at=datetime.now(UTC),
        ),
        output_dir=Path("/tmp"),
    )
    settings = ctx.resolved_voice_settings()
    assert settings is not None
    assert settings.language_code == "es-419"
    assert settings.stability == 0.5
    # tts_model es turbo cuando audio_language está seteado.
    assert ctx.tts_model == DEFAULT_TURBO_MODEL


async def test_tts_model_none_when_no_audio_language() -> None:
    """Sin `audio_language`, no se fuerza turbo (usa default multilingual)."""
    ctx = WorkflowExecutionContext(
        audio_language=None,
        voice_id="voice_x",
        voice_settings=None,
        base_image_ref=ImageAssetRef(
            kind=ImageAssetKind.GENERATED,
            id="x",
            label="x",
            kie_url="https://x",
            expires_at=datetime.now(UTC),
        ),
        output_dir=Path("/tmp"),
    )
    assert ctx.tts_model is None
