"""Composition root: arma el grafo de dependencias y monta la `App` Textual.

Único lugar que conoce las clases concretas de `infra/` (DIP). Las capas
superiores reciben `KieGateway` / `JobRepository` por inyección.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import ClassVar, Final

from loguru import logger
from textual.app import App
from textual.binding import Binding

from . import __version__
from .app_layer.audio_job_lifecycle import AudioJobLifecycle
from .app_layer.audio_job_runner import AudioJobRunner
from .app_layer.audio_player import AudioPlayer
from .app_layer.audios_controller import AudiosController
from .app_layer.batch_controller import BatchController
from .app_layer.generated_images_controller import GeneratedImagesController
from .app_layer.history_controller import HistoryController
from .app_layer.image_catalog_controller import ImageCatalogController
from .app_layer.image_job_lifecycle import ImageJobLifecycle
from .app_layer.image_job_runner import ImageJobRunner
from .app_layer.images_controller import ImagesController
from .app_layer.job_runner import JobRunner
from .app_layer.keys_controller import KeysController
from .app_layer.log_reader import LogReader
from .app_layer.notification_bridge import JobNotificationBridge
from .app_layer.presets_controller import VoicePresetsController
from .app_layer.queue_manager import QueueManager
from .app_layer.runner_factories import (
    AudioRunnerDeps,
    ImageRunnerDeps,
    WorkflowRunnerFactory,
)
from .app_layer.settings_controller import SettingsController
from .app_layer.system_opener import open_local_path, open_url
from .app_layer.update_checker import UpdateChecker
from .app_layer.video_job_lifecycle import VideoJobLifecycle
from .app_layer.videos_controller import VideosController
from .app_layer.workflow_base_resolver import WorkflowBaseResolver
from .app_layer.workflow_controller import WorkflowController
from .app_layer.workflow_lifecycle import WorkflowLifecycle
from .app_layer.workflow_runner import WorkflowRunner, WorkflowRunnerDeps
from .app_layer.workflow_step_runner import WorkflowStepRunner
from .config import Settings, load_settings
from .domain.events import (
    AudioJobUpdated,
    ImageJobUpdated,
    JobUpdated,
    WorkflowJobUpdated,
)
from .domain.models import (
    AUDIO_RESUMABLE_STATUSES,
    IMAGE_RESUMABLE_STATUSES,
    RESUMABLE_STATUSES,
    WORKFLOW_RESUMABLE_STATUSES,
    AudioJob,
    AudioJobStatus,
    BatchEntry,
    GeneratedAudio,
    GeneratedImage,
    GitHubRelease,
    ImageJob,
    ImageJobStatus,
    UploadedImage,
    VideoJob,
    WorkflowEntry,
    WorkflowJob,
    WorkflowStatus,
)
from .domain.ports import DesktopNotifier
from .infra.audio_downloader import download_audio
from .infra.audio_jobs_db import AudioJobsDB
from .infra.audios_db import AudiosDB
from .infra.batch_loader import scan_batch_dir
from .infra.db import JobsDB
from .infra.env_writer import DotenvWriter
from .infra.generated_images_db import GeneratedImagesDB
from .infra.github_releases import get_latest_release
from .infra.image_jobs_db import ImageJobsDB
from .infra.images_db import ImagesDB
from .infra.keys_store import KEYS_FILE_NAME, KeysStore
from .infra.kie_client import KieClient
from .infra.logging import (
    bridge_stdlib_logging,
    configure_logging,
    install_asyncio_exception_handler,
)
from .infra.notifier import NullNotifier, SystemNotifier
from .infra.presets_store import VoicePresetsStore
from .infra.workflow_db import WorkflowDB
from .infra.workflow_loader import build_workflow_from_entry, scan_workflows_dir
from .infra.workflow_manifest_writer import AtomicWorkflowManifestWriter
from .ui.menu import MENU_BY_ID, MenuItem
from .ui.screens.audios import AudiosScreen
from .ui.screens.automation import AutomationScreen
from .ui.screens.batch import BatchScreen
from .ui.screens.history import HistoryScreen
from .ui.screens.images import ImagesScreen
from .ui.screens.logs import LogsScreen
from .ui.screens.main_menu import MainMenuScreen
from .ui.screens.presets import PresetsScreen
from .ui.screens.queue import QueueScreen
from .ui.screens.settings import SettingsScreen
from .ui.screens.videos import VideosScreen

# Duraciones de las notificaciones (segundos). No son timeouts de red ni cuentan
# para CR-5.* — son tiempos cosméticos de toast.
_NOTIFY_RECOVERY_TIMEOUT: Final[int] = 5
_NOTIFY_PENDING_TIMEOUT: Final[int] = 4
_NOTIFY_RELOAD_TIMEOUT: Final[int] = 5
_NOTIFY_ERROR_TIMEOUT: Final[int] = 8
_NOTIFY_UPDATE_TIMEOUT: Final[int] = 15

_QUIT_ITEM_ID: Final[str] = "quit"
_SETTINGS_ITEM_ID: Final[str] = "settings"
_LOGS_ITEM_ID: Final[str] = "logs"
_IMAGES_ITEM_ID: Final[str] = "images"
_AUDIOS_ITEM_ID: Final[str] = "audios"
_HISTORY_ITEM_ID: Final[str] = "history"
_NEW_JOB_ITEM_ID: Final[str] = "new_job"
_QUEUE_ITEM_ID: Final[str] = "queue"
_PRESETS_ITEM_ID: Final[str] = "presets"
_BATCH_ITEM_ID: Final[str] = "batch"
_AUTOMATION_ITEM_ID: Final[str] = "automation"

_ENV_FILE_NAME: Final[str] = ".env"

# Subdirs del `data_dir` para cachear MP3s descargados (los servidores de Kie
# devuelven `Content-Disposition: attachment`, así que abrir la URL directa
# dispara descarga del navegador en vez de reproducción). Cacheamos
# permanentemente porque ni los previews built-in ni los audios generados
# cambian durante su TTL en Kie.
_VOICE_PREVIEW_CACHE_SUBDIR: Final[str] = "voice_previews"
_AUDIO_CACHE_SUBDIR: Final[str] = "audio_cache"


class KieAvatarStudioApp(App[None]):
    CSS_PATH = "ui/styles.tcss"
    # Theme inspirado en OpenCode / Claude Code: oscuro con acentos
    # cyan/magenta. Built-in en Textual; los tokens `$primary`, `$accent`,
    # `$boost`, etc. del CSS se resuelven automáticamente contra esta paleta.
    # Cambiable en runtime con `self.theme = "<otro>"` (lo expondremos en
    # Settings cuando alguien lo pida).
    TITLE = "Kie Avatar Studio"
    SUB_TITLE = f"v{__version__} · TUI local · Kie.ai"

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("n", "select('new_job')", "Nuevo"),
        Binding("b", "select('batch')", "Lote"),
        Binding("f", "select('automation')", "Auto"),
        Binding("g", "select('queue')", "Cola"),
        Binding("h", "select('history')", "Historial"),
        Binding("p", "select('presets')", "Presets"),
        Binding("c", "select('settings')", "Config"),
        Binding("l", "select('logs')", "Logs"),
        Binding("i", "select('images')", "Imágenes"),
        Binding("a", "select('audios')", "Audios"),
        Binding("q", "quit", "Salir"),
        Binding("x", "quit", "Salir", show=False),
        Binding("ctrl+c", "quit", "Salir", show=False),
    ]

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__()
        self.theme = "tokyo-night"
        self.settings = settings or load_settings()
        self.log_file: Path = configure_logging(
            self.settings.logs_dir,
            self.settings.log_level,
            tui_mode=True,
        )
        bridge_stdlib_logging(self.settings.log_level)
        self.db = JobsDB(self.settings.db_path)
        self.images_db = ImagesDB(self.settings.db_path)
        self.audios_db = AudiosDB(self.settings.db_path)
        self.audio_jobs_db = AudioJobsDB(self.settings.db_path)
        self.image_jobs_db = ImageJobsDB(self.settings.db_path)
        self.generated_images_db = GeneratedImagesDB(self.settings.db_path)
        self.presets_store = VoicePresetsStore(self.settings.presets_dir)
        self.keys_store = KeysStore(self.settings.data_dir / KEYS_FILE_NAME)
        self.env_writer = DotenvWriter(self.settings.data_dir.parent / _ENV_FILE_NAME)
        self.kie = self._build_kie_client()
        self.runner = JobRunner(self.settings, self.kie, self.db)
        # Lifecycle de VideoJob separado del QueueManager para que el queue
        # sea type-agnostic (reutilizable con AudioJob/ImageJob).
        self.video_lifecycle = VideoJobLifecycle(self.db)
        # Semáforo único compartido entre las tres colas: garantiza que el
        # límite global `max_parallel_jobs` no se viole cuando hay video,
        # audio e imagen corriendo en paralelo. Sin esto, cada queue
        # tendría su propio contador y podríamos llegar al triple del
        # límite real.
        self._capacity_limiter = asyncio.Semaphore(max(1, self.settings.max_parallel_jobs))
        self.queue: QueueManager[VideoJob, JobUpdated] = QueueManager(
            self.settings,
            self.runner,
            event_factory=JobUpdated,
            lifecycle=self.video_lifecycle,
            capacity_limiter=self._capacity_limiter,
        )
        self.audio_lifecycle = AudioJobLifecycle(self.audio_jobs_db)
        self.audio_runner = AudioJobRunner(
            self.settings, self.kie, self.audio_jobs_db, self.audios_db
        )
        self.audio_queue: QueueManager[AudioJob, AudioJobUpdated] = QueueManager(
            self.settings,
            self.audio_runner,
            event_factory=AudioJobUpdated,
            lifecycle=self.audio_lifecycle,
            capacity_limiter=self._capacity_limiter,
        )
        self.image_lifecycle = ImageJobLifecycle(self.image_jobs_db)
        self.image_runner = ImageJobRunner(
            self.settings,
            self.kie,
            self.image_jobs_db,
            self.generated_images_db,
            self.images_db,
        )
        self.image_queue: QueueManager[ImageJob, ImageJobUpdated] = QueueManager(
            self.settings,
            self.image_runner,
            event_factory=ImageJobUpdated,
            lifecycle=self.image_lifecycle,
            capacity_limiter=self._capacity_limiter,
        )
        # Workflow subsystem: limiter PROPIO (no comparte el global de Kie)
        # para evitar deadlock cuando el orquestador toma un slot esperando
        # que sus sub-jobs (que también compiten por el global) terminen.
        self.workflow_db = WorkflowDB(self.settings.db_path)
        self.workflow_manifest_writer = AtomicWorkflowManifestWriter()
        self.workflow_runner_factory = WorkflowRunnerFactory(
            image_deps=ImageRunnerDeps(
                settings=self.settings,
                client=self.kie,
                image_jobs_repo=self.image_jobs_db,
                generated_images_store=self.generated_images_db,
                uploaded_images_store=self.images_db,
            ),
            audio_deps=AudioRunnerDeps(
                settings=self.settings,
                client=self.kie,
                audio_jobs_repo=self.audio_jobs_db,
                audios_store=self.audios_db,
            ),
        )
        self.workflow_step_runner = WorkflowStepRunner(
            self.settings,
            self.kie,
            self._capacity_limiter,
            image_jobs_repo=self.image_jobs_db,
            generated_images_store=self.generated_images_db,
            runner_factory=self.workflow_runner_factory,
        )
        self.workflow_base_resolver = WorkflowBaseResolver(
            self.settings,
            self.kie,
            self.presets_store,
            self.images_db,
            self.generated_images_db,
            self.image_jobs_db,
            self._capacity_limiter,
            self.workflow_runner_factory,
        )
        self.workflow_runner = WorkflowRunner(
            self.settings,
            self.kie,
            WorkflowRunnerDeps(
                repository=self.workflow_db,
                manifest_writer=self.workflow_manifest_writer,
                step_runner=self.workflow_step_runner,
                base_resolver=self.workflow_base_resolver,
            ),
        )
        self.workflow_lifecycle = WorkflowLifecycle(self.workflow_db)
        self._workflows_limiter = asyncio.Semaphore(
            max(1, self.settings.max_parallel_workflows)
        )
        self.workflow_queue: QueueManager[WorkflowJob, WorkflowJobUpdated] = QueueManager(
            self.settings,
            self.workflow_runner,
            event_factory=WorkflowJobUpdated,
            lifecycle=self.workflow_lifecycle,
            capacity_limiter=self._workflows_limiter,
        )
        # El runner emite eventos via callback (porque cada transición de
        # step necesita reescribir el manifest + notificar). Conectamos
        # ese callback al queue:
        self.workflow_runner.set_notify(self._dispatch_workflow_event)
        self.keys_controller = KeysController(self.keys_store, self._gateway_factory)
        self.settings_controller = SettingsController(self.settings, self.env_writer)
        self.images_controller = ImagesController(self.images_db, self.kie)
        self.audios_controller = AudiosController(
            self.audios_db,
            self.audio_jobs_db,
            self.audio_queue,
        )
        self.generated_images_controller = GeneratedImagesController(
            self.generated_images_db,
            self.image_jobs_db,
            self.image_queue,
        )
        self.image_catalog = ImageCatalogController(self.images_db, self.generated_images_db)
        self.videos_controller = VideosController(
            self.db,
            self.image_catalog,
            self.audios_db,
            self.queue,
        )
        self.history_controller = HistoryController(
            self.db,
            self.audio_jobs_db,
            self.image_jobs_db,
            self.queue,
            self.audio_queue,
            self.image_queue,
        )
        self.presets_controller = VoicePresetsController(self.presets_store)
        self.batch_controller = BatchController(
            scan_loader=self._scan_batch_dir,
            videos_controller=self.videos_controller,
        )
        self.workflow_controller = WorkflowController(
            self.settings,
            self.workflow_db,
            self.workflow_manifest_writer,
            self.workflow_queue,
            scan_loader=self._scan_workflows_dir,
            entry_builder=build_workflow_from_entry,
            presets_store=self.presets_store,
            uploaded_images=self.images_db,
            generated_images=self.generated_images_db,
        )
        self.notifier: DesktopNotifier = (
            SystemNotifier() if self.settings.notifications_enabled else NullNotifier()
        )
        self.notification_bridge = JobNotificationBridge(self.notifier)
        self.update_checker = UpdateChecker(
            current_version=__version__,
            fetch_latest=self._fetch_latest_release,
        )
        self.audio_player = AudioPlayer(
            downloader=download_audio,
            voice_preview_dir=self.settings.data_dir / _VOICE_PREVIEW_CACHE_SUBDIR,
            audio_cache_dir=self.settings.data_dir / _AUDIO_CACHE_SUBDIR,
        )
        self.log_reader = LogReader(self.log_file)

    async def on_mount(self) -> None:
        install_asyncio_exception_handler()
        logger.info("Kie Avatar Studio arrancando (log_file={})", self.log_file)
        await self._init_stores()
        await self._apply_active_key_if_any()
        self._warn_if_no_api_key()
        self._wire_background_workers()
        expired_images = await self.images_controller.cleanup_expired()
        expired_audios = await self.audios_controller.cleanup_expired()
        expired_generated_images = await self.generated_images_controller.cleanup_expired()

        # `restore_pending` ahora recibe un loader callable (no el repo
        # directo): así el QueueManager no conoce JobRepository concreto.
        async def load_resumable_video_jobs() -> list[VideoJob]:
            jobs: list[VideoJob] = []
            for status in RESUMABLE_STATUSES:
                jobs.extend(await self.db.list_by_status(status))
            return jobs

        recovered = await self.queue.restore_pending(load_resumable_video_jobs)

        # Antes de restaurar audios, marcamos los que quedaron en CREATING
        # (crash entre `create_tts_task` y persistir `task_id`) como FAILED:
        # no podemos saber si el POST llegó a Kie ni reanudar sin task_id
        # → más seguro pedir reintento manual que duplicar créditos.
        await self._mark_creating_audio_jobs_as_failed()

        async def load_resumable_audio_jobs() -> list[AudioJob]:
            jobs: list[AudioJob] = []
            for status in AUDIO_RESUMABLE_STATUSES:
                jobs.extend(await self.audio_jobs_db.list_by_status(status))
            return jobs

        recovered_audio = await self.audio_queue.restore_pending(load_resumable_audio_jobs)

        # Mismo razonamiento que con audio: CREATING en image es estado
        # indeterminado tras crash → FAILED para que el usuario decida.
        await self._mark_creating_image_jobs_as_failed()

        async def load_resumable_image_jobs() -> list[ImageJob]:
            jobs: list[ImageJob] = []
            for status in IMAGE_RESUMABLE_STATUSES:
                jobs.extend(await self.image_jobs_db.list_by_status(status))
            return jobs

        recovered_image = await self.image_queue.restore_pending(load_resumable_image_jobs)

        # Workflows: por simplicidad/seguridad, los workflows en estados
        # no terminales al arrancar se marcan FAILED. Cada sub-job hoja
        # tiene su propio mecanismo de recovery (image_jobs, audio_jobs),
        # pero el orquestador del workflow es stateful (locks, paralelismo)
        # y reanudarlo a mitad sin haber persistido el step en ejecución
        # podría duplicar trabajo. El usuario reintenta manualmente.
        recovered_workflow = await self._mark_running_workflows_as_failed()
        # Regeneramos el manifest de cada workflow tocado para que un
        # consumer externo NO vea snapshot stale del crash anterior.
        await self._regenerate_workflow_manifests()

        screen = MainMenuScreen(on_select=self._handle_menu_selection)
        await self.push_screen(screen)
        self._notify_startup_summary(
            expired_images=expired_images,
            expired_audios=expired_audios,
            expired_generated_images=expired_generated_images,
            recovered=recovered,
            recovered_audio=recovered_audio,
            recovered_image=recovered_image,
            recovered_workflow=recovered_workflow,
        )

    async def _init_stores(self) -> None:
        """Inicializa todas las bases de datos y stores file-based en orden."""
        await self.db.init()
        await self.images_db.init()
        await self.audios_db.init()
        await self.audio_jobs_db.init()
        await self.image_jobs_db.init()
        await self.generated_images_db.init()
        await self.workflow_db.init()
        await self.presets_store.init()
        await self.keys_store.init()

    def _warn_if_no_api_key(self) -> None:
        if not self.settings.kie_api_key:
            logger.warning(
                "KIE_API_KEY vacío y no hay key activa en data/keys.json: "
                "las llamadas a Kie fallarán hasta configurar una en Configuración (c)."
            )

    def _wire_background_workers(self) -> None:
        """Suscribe el bridge de notificaciones y lanza el update check.

        Listener idempotente por job_id en el bridge (no spam si múltiples
        pantallas se subscriben al mismo evento). El update check es
        opcional via `settings.update_check_enabled`.
        """
        self.queue.add_listener(self.notification_bridge.on_video_event)
        self.audio_queue.add_listener(self.notification_bridge.on_audio_event)
        self.image_queue.add_listener(self.notification_bridge.on_image_event)
        self.workflow_queue.add_listener(self.notification_bridge.on_workflow_event)
        if self.settings.update_check_enabled:
            self.run_worker(self._check_for_update(), exclusive=True)

    def _dispatch_workflow_event(self, workflow: WorkflowJob) -> None:
        """Callback que el WorkflowRunner invoca tras cada transición.

        Lo propagamos a los listeners del `workflow_queue` así las
        pantallas UI reaccionan a las transiciones de los steps
        individuales (no solo al final del run).
        """
        self.workflow_queue.notify_external(workflow)

    def _notify_startup_summary(
        self,
        *,
        expired_images: list[UploadedImage],
        expired_audios: list[GeneratedAudio],
        expired_generated_images: list[GeneratedImage],
        recovered: int,
        recovered_audio: int,
        recovered_image: int,
        recovered_workflow: int = 0,
    ) -> None:
        if expired_images:
            self.notify(
                f"Se quitaron {len(expired_images)} imágenes ya expiradas "
                f"({self.images_controller.retention_hours}h en Kie).",
                title="Limpieza",
                timeout=_NOTIFY_RECOVERY_TIMEOUT,
            )
        if expired_audios:
            self.notify(
                f"Se quitaron {len(expired_audios)} audios ya expirados "
                f"({self.audios_controller.retention_days}d en Kie).",
                title="Limpieza",
                timeout=_NOTIFY_RECOVERY_TIMEOUT,
            )
        if expired_generated_images:
            self.notify(
                f"Se quitaron {len(expired_generated_images)} imágenes generadas ya expiradas "
                f"({self.generated_images_controller.retention_days}d en Kie).",
                title="Limpieza",
                timeout=_NOTIFY_RECOVERY_TIMEOUT,
            )
        if recovered:
            self.notify(
                f"Se reanudaron {recovered} jobs pendientes desde la última sesión.",
                title="Recuperación",
                timeout=_NOTIFY_RECOVERY_TIMEOUT,
            )
        if recovered_audio:
            self.notify(
                f"Se reanudaron {recovered_audio} audio jobs pendientes.",
                title="Recuperación",
                timeout=_NOTIFY_RECOVERY_TIMEOUT,
            )
        if recovered_image:
            self.notify(
                f"Se reanudaron {recovered_image} image jobs pendientes.",
                title="Recuperación",
                timeout=_NOTIFY_RECOVERY_TIMEOUT,
            )
        if recovered_workflow:
            self.notify(
                f"Se marcaron {recovered_workflow} workflows como FAILED tras el "
                "reinicio (estado indeterminado, reintentalos manualmente).",
                title="Recuperación",
                timeout=_NOTIFY_RECOVERY_TIMEOUT,
            )

    async def _mark_creating_audio_jobs_as_failed(self) -> None:
        """Sanea audio jobs que quedaron en CREATING tras un crash.

        Estado indeterminado: el POST a Kie puede haber llegado o no. Sin
        `task_id` no podemos reanudar (no sabemos qué pollear) ni reintentar
        sin riesgo de duplicar créditos. Política conservadora: marcarlos
        FAILED para que el usuario decida si reintenta manualmente.
        """
        stuck = await self.audio_jobs_db.list_by_status(AudioJobStatus.CREATING)
        for job in stuck:
            logger.warning(
                "AudioJob {} ('{}') estaba en CREATING al arrancar; marcando FAILED "
                "(estado indeterminado, posible crédito consumido)",
                job.id,
                job.label,
            )
            job.status = AudioJobStatus.FAILED
            job.error = (
                "Estado indeterminado al reiniciar: el task pudo o no haberse "
                "creado en Kie. Verificá tu saldo antes de reintentar."
            )
            await self.audio_jobs_db.upsert(job)

    async def _mark_creating_image_jobs_as_failed(self) -> None:
        """Sanea image jobs que quedaron en CREATING tras un crash.

        Mismo razonamiento que `_mark_creating_audio_jobs_as_failed`: sin
        `task_id` persistido no podemos saber si el `createTask` de Kie
        llegó a registrar el job. Reintentar a ciegas duplicaría créditos.
        """
        stuck = await self.image_jobs_db.list_by_status(ImageJobStatus.CREATING)
        for job in stuck:
            logger.warning(
                "ImageJob {} ('{}') estaba en CREATING al arrancar; marcando FAILED "
                "(estado indeterminado, posible crédito consumido)",
                job.id,
                job.label,
            )
            job.status = ImageJobStatus.FAILED
            job.error = (
                "Estado indeterminado al reiniciar: el task pudo o no haberse "
                "creado en Kie. Verificá tu saldo antes de reintentar."
            )
            await self.image_jobs_db.upsert(job)

    async def _mark_running_workflows_as_failed(self) -> int:
        """Marca como FAILED los workflows que quedaron en estado no terminal.

        Los workflows son stateful (locks por workflow, paralelismo entre
        steps, manifest derivado) y reanudarlos a mitad sin perder
        consistencia es complejo. Política conservadora: el usuario los
        reintenta manualmente. Cada sub-job hoja (image/audio) tiene su
        propio mecanismo de recovery o se marcó FAILED arriba.
        """
        count = 0
        for status in WORKFLOW_RESUMABLE_STATUSES:
            stuck = await self.workflow_db.list_by_status(status)
            for workflow in stuck:
                logger.warning(
                    "WorkflowJob {} ('{}') estaba en {} al arrancar; marcando FAILED "
                    "(reintentar manualmente — sub-jobs hoja pudieron quedar a medias)",
                    workflow.id,
                    workflow.name,
                    workflow.status.value,
                )
                workflow.status = WorkflowStatus.FAILED
                workflow.error = (
                    "App reiniciada con el workflow en ejecución. Reintentá "
                    "manualmente. Revisá los outputs ya generados antes de "
                    "re-ejecutar para no duplicar créditos."
                )
                await self.workflow_db.update_workflow_header(workflow)
                count += 1
        return count

    async def _regenerate_workflow_manifests(self) -> None:
        """Re-escribe `workflow.json` de todos los workflows persistidos.

        Garantiza que el snapshot en disco refleje el estado de la DB
        tras el restart (sino quedaría stale de antes del crash).
        Best-effort: si una escritura falla, se loguea y se sigue.
        """
        recent = await self.workflow_db.list_recent(limit=100)
        for workflow in recent:
            ok = await self.workflow_manifest_writer.write(workflow)
            if not ok:
                logger.debug(
                    "No se pudo regenerar manifest de workflow {} ({})",
                    workflow.id,
                    workflow.name,
                )

    async def on_unmount(self) -> None:
        await self.audio_player.stop()
        await self.queue.drain()
        await self.audio_queue.drain()
        await self.image_queue.drain()
        await self.workflow_queue.drain()
        await self.kie.aclose()
        logger.info("Kie Avatar Studio cerrado limpiamente")

    def _handle_exception(self, error: Exception) -> None:
        """Override de Textual: loguea con traceback completo + notifica al usuario.

        Sin esto, las excepciones no manejadas dentro de handlers de la TUI
        terminan en stderr (oculto bajo el alt-screen) y se pierden.
        """
        logger.opt(exception=error).error("Excepción no manejada en la TUI")
        try:
            self.notify(
                f"Error: {error.__class__.__name__}: {error}. Detalle en {self.log_file}",
                title="Error inesperado",
                severity="error",
                timeout=_NOTIFY_ERROR_TIMEOUT,
            )
        except Exception:
            logger.exception("Falló notify del propio handler de error")
        super()._handle_exception(error)

    def action_select(self, item_id: str) -> None:
        """Atajos globales (N, B, G, H, P, C, L). Resuelve por id O(1)."""
        self._handle_menu_selection(item_id)

    # --- handler único de selección ---------------------------------------

    def _handle_menu_selection(self, item_id: str) -> None:  # noqa: PLR0911, PLR0912, C901 — dispatch
        item: MenuItem | None = MENU_BY_ID.get(item_id)
        if item is None:
            return
        if item.id == _QUIT_ITEM_ID:
            self.exit()
            return
        if item.id == _SETTINGS_ITEM_ID:
            self.push_screen(
                SettingsScreen(
                    keys_controller=self.keys_controller,
                    settings_controller=self.settings_controller,
                    on_kie_credentials_changed=self._reload_kie_client,
                    on_endpoints_changed=self._reload_kie_client_after_env_change,
                )
            )
            return
        if item.id == _LOGS_ITEM_ID:
            self.push_screen(LogsScreen(self.log_reader))
            return
        if item.id == _IMAGES_ITEM_ID:
            self.push_screen(
                ImagesScreen(
                    uploads_controller=self.images_controller,
                    generated_controller=self.generated_images_controller,
                    image_catalog=self.image_catalog,
                    open_local_path=open_local_path,
                    open_url=open_url,
                    default_input_dir=self.settings.inputs_dir,
                    check_credits=self._check_credits,
                )
            )
            return
        if item.id == _AUDIOS_ITEM_ID:
            self.push_screen(
                AudiosScreen(
                    controller=self.audios_controller,
                    audio_player=self.audio_player,
                    presets_controller=self.presets_controller,
                    check_credits=self._check_credits,
                )
            )
            return
        if item.id == _HISTORY_ITEM_ID:
            self.push_screen(HistoryScreen(controller=self.history_controller))
            return
        if item.id == _QUEUE_ITEM_ID:
            self.push_screen(
                QueueScreen(
                    history_controller=self.history_controller,
                    audios_controller=self.audios_controller,
                    videos_controller=self.videos_controller,
                    generated_images_controller=self.generated_images_controller,
                )
            )
            return
        if item.id == _NEW_JOB_ITEM_ID:
            self.push_screen(
                VideosScreen(
                    videos_controller=self.videos_controller,
                    image_catalog=self.image_catalog,
                    audios_controller=self.audios_controller,
                    audio_player=self.audio_player,
                    open_local_path=open_local_path,
                    open_url=open_url,
                )
            )
            return
        if item.id == _PRESETS_ITEM_ID:
            self.push_screen(PresetsScreen(controller=self.presets_controller))
            return
        if item.id == _BATCH_ITEM_ID:
            self.push_screen(
                BatchScreen(
                    controller=self.batch_controller,
                    batch_dir=str(self.settings.batch_jobs_dir),
                )
            )
            return
        if item.id == _AUTOMATION_ITEM_ID:
            self.push_screen(
                AutomationScreen(
                    controller=self.workflow_controller,
                    workflows_dir=str(self.settings.workflows_dir),
                )
            )
            return
        if item.pending_message:
            self.notify(
                item.pending_message,
                title="Próximamente",
                severity="warning",
                timeout=_NOTIFY_PENDING_TIMEOUT,
            )

    async def _check_credits(self) -> float | None:
        """Best-effort: consulta saldo de la key activa de Kie.

        Devuelve `None` si no hay key activa, si la red falla o si Kie
        devuelve un error: las pantallas que llaman a esto **no deben**
        considerarlo error de la app. El indicador de saldo es informativo.
        """
        if not self.settings.kie_api_key:
            return None
        try:
            return await self.kie.get_account_credits()
        except Exception:
            logger.opt(exception=True).debug("Consulta de saldo de Kie falló (best-effort)")
            return None

    async def _scan_batch_dir(self) -> list[BatchEntry]:
        """Closure cableada al `BatchController`: escanea con defaults vivos.

        Releemos `self.settings.default_prompt` / `default_voice` en cada
        llamada porque pueden cambiar en runtime si el usuario edita
        Configuración. Si el directorio no existe, devolvemos lista vacía
        (no es error: simplemente no hay lotes).
        """
        return await scan_batch_dir(
            self.settings.batch_jobs_dir,
            default_prompt=self.settings.default_prompt,
            default_voice=self.settings.default_voice,
        )

    async def _scan_workflows_dir(self) -> list[WorkflowEntry]:
        """Closure cableada al `WorkflowController`: escanea `workflows/`."""
        return await scan_workflows_dir(self.settings.workflows_dir)

    # --- updater ----------------------------------------------------------

    async def _fetch_latest_release(self) -> GitHubRelease | None:
        """Closure cableada al `UpdateChecker`: usa el repo configurado.

        Parsea `owner/repo` del setting. Si el formato no es válido
        (ej. el usuario lo dejó vacío), devuelve None silenciosamente.
        """
        repo_spec = self.settings.update_check_repo
        if "/" not in repo_spec:
            return None
        owner, repo = repo_spec.split("/", 1)
        return await get_latest_release(owner.strip(), repo.strip())

    async def _check_for_update(self) -> None:
        """Worker: chequea si hay nueva versión y notifica al usuario.

        Best-effort: cualquier error se atrapa para que el updater nunca
        rompa la app. El toast aparece UNA VEZ por sesión (no spam).
        """
        try:
            result = await self.update_checker.check()
        except Exception:
            logger.opt(exception=True).debug("Update check falló (best-effort)")
            return
        if result is None:
            return
        self.notify(
            f"Nueva versión {result.latest_version} disponible "
            f"(tenés {result.current_version}). Descargala desde:\n{result.release_url}",
            title="🆙 Actualización disponible",
            timeout=_NOTIFY_UPDATE_TIMEOUT,
        )

    # --- construcción y reload del cliente HTTP ---------------------------

    def _build_kie_client(self) -> KieClient:
        return KieClient(self.settings)

    def _gateway_factory(self, secret: str) -> KieClient:
        """Construye un `KieClient` ad-hoc con la key dada (para `test_key`)."""
        adhoc_settings = self.settings.model_copy(update={"kie_api_key": secret})
        return KieClient(adhoc_settings)

    async def _apply_active_key_if_any(self) -> None:
        active = await self.keys_store.get_active()
        if active is None or active.key == self.settings.kie_api_key:
            return
        self.settings = self.settings.model_copy(update={"kie_api_key": active.key})
        await self._rebuild_kie_client()

    async def _reload_kie_client(self) -> None:
        """Aplica la nueva key activa al `KieClient` en uso."""
        active = await self.keys_store.get_active()
        new_secret = active.key if active else ""
        if new_secret == self.settings.kie_api_key:
            return
        self.settings = self.settings.model_copy(update={"kie_api_key": new_secret})
        await self._rebuild_kie_client()
        self.notify(
            "Credenciales de Kie recargadas. Los próximos jobs usarán la nueva key.",
            title="Configuración",
            timeout=_NOTIFY_RELOAD_TIMEOUT,
        )

    async def _reload_kie_client_after_env_change(self) -> None:
        """Releerá el `.env` y reconstruirá `KieClient` con endpoints nuevos."""
        new_settings = load_settings()
        # Preservar la key activa elegida en runtime (no la del .env).
        new_settings = new_settings.model_copy(update={"kie_api_key": self.settings.kie_api_key})
        self.settings = new_settings
        await self._rebuild_kie_client()
        self.notify(
            "Endpoints recargados. Los próximos jobs usan la nueva configuración.",
            title="Configuración",
            timeout=_NOTIFY_RELOAD_TIMEOUT,
        )

    async def _rebuild_kie_client(self) -> None:
        old = self.kie
        self.kie = self._build_kie_client()
        # `JobRunner`, `AudioJobRunner` e `ImageJobRunner` toman el cliente
        # en cada `run()` desde `self._client`, así que solo necesitamos
        # rebindear las referencias que usan.
        self.runner = JobRunner(self.settings, self.kie, self.db)
        self.queue = QueueManager(
            self.settings,
            self.runner,
            event_factory=JobUpdated,
            lifecycle=self.video_lifecycle,
            capacity_limiter=self._capacity_limiter,
        )
        self.audio_runner = AudioJobRunner(
            self.settings, self.kie, self.audio_jobs_db, self.audios_db
        )
        self.audio_queue = QueueManager(
            self.settings,
            self.audio_runner,
            event_factory=AudioJobUpdated,
            lifecycle=self.audio_lifecycle,
            capacity_limiter=self._capacity_limiter,
        )
        self.image_runner = ImageJobRunner(
            self.settings,
            self.kie,
            self.image_jobs_db,
            self.generated_images_db,
            self.images_db,
        )
        self.image_queue = QueueManager(
            self.settings,
            self.image_runner,
            event_factory=ImageJobUpdated,
            lifecycle=self.image_lifecycle,
            capacity_limiter=self._capacity_limiter,
        )
        # IMPORTANTE: el `AudiosController` guarda una referencia concreta
        # al queue. Si solo rebindeamos `self.audio_queue` y dejamos el
        # controller apuntando al queue viejo, los próximos enqueue desde
        # la UI van al runner viejo (que ya usa un KieClient cerrado).
        # Hay que recrear el controller con el nuevo queue. Mismo motivo
        # para `HistoryController`, `VideosController` y
        # `GeneratedImagesController` (referencias a los queues).
        self.images_controller = ImagesController(self.images_db, self.kie)
        self.audios_controller = AudiosController(
            self.audios_db,
            self.audio_jobs_db,
            self.audio_queue,
        )
        self.generated_images_controller = GeneratedImagesController(
            self.generated_images_db,
            self.image_jobs_db,
            self.image_queue,
        )
        self.image_catalog = ImageCatalogController(self.images_db, self.generated_images_db)
        self.videos_controller = VideosController(
            self.db,
            self.image_catalog,
            self.audios_db,
            self.queue,
        )
        self.history_controller = HistoryController(
            self.db,
            self.audio_jobs_db,
            self.image_jobs_db,
            self.queue,
            self.audio_queue,
            self.image_queue,
        )
        await old.aclose()
