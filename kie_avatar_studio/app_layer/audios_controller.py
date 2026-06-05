"""Controller para administrar audios TTS persistidos.

Después de la etapa 3 del refactor de cola estructurada, este controller
ya no hace HTTP directo: solo orquesta la persistencia local y delega la
ejecución al `audio_queue` (que internamente usa `AudioJobRunner` para
crear el task en Kie + polling).

Casos de uso que sigue manejando:
- `enqueue_generation`: valida, persiste el `AudioJob` y lo encola.
- `wait_for_job`: helper opcional para que la UI espere a que un job
  encolado termine sin tener que cablear listeners por su cuenta.
- `subscribe`: registra un listener al stream de `AudioJobUpdated` y
  devuelve un callable para desuscribir. Pensado para que las pantallas
  refresquen en vivo cuando cambia el estado de algún job.
- `cancel` / `retry` para jobs activos o terminales fallidos.
- `list_generated` / `get` / `get_for_use` sobre `GeneratedAudio` (audios
  ya terminados, listos para reutilizar en jobs de video).
- `cleanup_expired`: barre audios cuya retención en Kie ya venció.

La validación granular del payload (script, voice_id, settings) la hace
`AudioJobRunner` en su step `VALIDATING`. Acá solo validamos lo que es
estrictamente de "registro local" (label no vacío).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import Final

from loguru import logger

from ..domain.errors import AudioExpiredError, AudioNotFoundError, AudioValidationError
from ..domain.events import AudioJobUpdated
from ..domain.models import AudioJob, AudioJobStatus, GeneratedAudio, VoiceSettings
from ..domain.policies import KIE_GENERATED_RETENTION_DAYS
from ..domain.ports import AudioJobRepository, AudioStore
from .ids import new_audio_id
from .queue_manager import QueueManager

_LABEL_MAX_LENGTH: Final[int] = 64

AudioEventListener = (
    Callable[[AudioJobUpdated], None] | Callable[[AudioJobUpdated], Awaitable[None]]
)


class AudiosController:
    """Casos de uso sobre audios persistidos. Encola generación; no espera el resultado."""

    def __init__(
        self,
        store: AudioStore,
        audio_jobs_repo: AudioJobRepository,
        queue: QueueManager[AudioJob, AudioJobUpdated],
        *,
        retention_days: int = KIE_GENERATED_RETENTION_DAYS,
    ) -> None:
        self._store = store
        self._jobs_repo = audio_jobs_repo
        self._queue = queue
        self._retention_days = retention_days

    @property
    def retention_days(self) -> int:
        return self._retention_days

    async def list_generated(self) -> list[GeneratedAudio]:
        return await self._store.list_recent()

    async def list_audio_jobs(self, limit: int = 50) -> list[AudioJob]:
        """Lista los audio jobs recientes (en cola, generando, terminales)."""
        return await self._jobs_repo.list_recent(limit)

    async def get_audio_job(self, job_id: str) -> AudioJob | None:
        """Devuelve un `AudioJob` por id (en cualquier estado) o None."""
        return await self._jobs_repo.get(job_id)

    async def get(self, audio_id: str) -> GeneratedAudio | None:
        return await self._store.get(audio_id)

    async def get_for_use(self, audio_id: str) -> GeneratedAudio:
        """Devuelve el audio listo para reutilizar en un job.

        Lanza:
        - `AudioNotFoundError` si no existe en el store local.
        - `AudioExpiredError` si ya superó la ventana de retención de Kie
          (el archivo en `kie_url` ya fue auto-borrado por el proveedor).
        """
        audio = await self._store.get(audio_id)
        if audio is None:
            raise AudioNotFoundError(f"no existe ningún audio con id={audio_id!r}")
        if audio.is_expired(self._retention_days):
            raise AudioExpiredError(
                f"el audio '{audio.label}' expiró en Kie hace "
                f"{-audio.time_left(self._retention_days)}; regeneralo."
            )
        return audio

    async def enqueue_generation(
        self,
        label: str,
        script: str,
        voice_id: str,
        voice_settings: VoiceSettings | None = None,
    ) -> AudioJob:
        """Crea un `AudioJob`, lo persiste como QUEUED y lo encola.

        No espera al resultado: el caller puede `await wait_for_job(job.id)`
        si necesita bloquear hasta el terminal, o suscribirse a
        `AudioJobUpdated` para feedback en vivo. La validación detallada
        (script, voice_id, settings) ocurre en `AudioJobRunner._validate`
        — acá solo limpiamos el label y devolvemos el job recién encolado.
        """
        clean_label = self._validate_label(label)
        settings_json: str | None = None
        if voice_settings is not None and not voice_settings.is_empty():
            settings_json = voice_settings.model_dump_json(exclude_none=True)
        job = AudioJob(
            id=new_audio_id(),
            label=clean_label,
            script=script,
            voice_id=voice_id,
            voice_settings_json=settings_json,
            status=AudioJobStatus.QUEUED,
        )
        await self._jobs_repo.upsert(job)
        self._queue.enqueue(job)
        logger.info("AudioJob '{}' encolado (id={})", clean_label, job.id)
        return job

    async def wait_for_job(self, job_id: str) -> AudioJob:
        """Bloquea hasta que el job llegue a un estado terminal.

        Registra un listener temporal en el queue filtrado por `job_id` y
        lo desuscribe apenas llega `is_terminal()`. Útil para la UI que
        quiere mostrar feedback "encolando → generando → listo" sin
        tener que escribir el cableado de listeners en cada caller.

        Si el job ya está en terminal cuando se llama (raro pero
        posible), devuelve inmediatamente leyendo el repo. El orden es
        importante: registramos el listener ANTES del lookup al repo
        para no perder eventos emitidos durante el await intermedio.
        """
        done: asyncio.Future[AudioJob] = asyncio.get_running_loop().create_future()

        def on_event(event: AudioJobUpdated) -> None:
            if event.job.id != job_id or done.done():
                return
            if event.job.is_terminal():
                done.set_result(event.job)

        unsubscribe = self._queue.add_listener(on_event)
        try:
            existing = await self._jobs_repo.get(job_id)
            if existing is not None and existing.is_terminal() and not done.done():
                done.set_result(existing)
            return await done
        finally:
            unsubscribe()

    def subscribe(self, callback: AudioEventListener) -> Callable[[], None]:
        """Registra un listener para eventos `AudioJobUpdated` en vivo.

        Devuelve un callable de desuscripción. Pensado para que la UI
        registre en `on_mount` y desuscriba en `on_unmount`. Acepta
        callbacks síncronos o async.
        """
        return self._queue.add_listener(callback)

    async def cancel(self, job_id: str) -> bool:
        """Cancela un audio job activo (o lo retira de la cola si está pendiente).

        Devuelve True si la operación cambió algo (estado pasó a CANCELLED),
        False si el job no existe o está en estado no-cancellable.
        """
        return await self._queue.cancel(job_id)

    async def retry(self, job_id: str) -> bool:
        """Reencola un audio job que está en estado FAILED o CANCELLED.

        Limpia `task_id` antes de reintentar (el viejo task pudo expirar
        en Kie tras 24h). Devuelve True si reencoló, False si no aplica.
        """
        job = await self._jobs_repo.get(job_id)
        if job is None:
            return False
        return await self._queue.retry(job)

    async def delete(self, audio_id: str) -> None:
        await self._store.delete(audio_id)

    async def delete_job(self, job_id: str) -> None:
        """Borra el `AudioJob` Y (si existe) el `GeneratedAudio` con el mismo id.

        Mismo id por idempotencia (ver `AudioJobRunner._finalize`). El
        borrado del `GeneratedAudio` es tolerante: si no existe, no
        rompe — algunos jobs (failed/cancelled) nunca lo generaron.
        """
        with contextlib.suppress(AudioNotFoundError):
            await self._store.delete(job_id)
        await self._jobs_repo.delete(job_id)

    async def cleanup_expired(self) -> list[GeneratedAudio]:
        """Borra del store local todos los audios cuyo TTL ya venció en Kie.

        Devuelve la lista de audios quitados para que el caller pueda
        notificar/loguear. Idempotente: llamarla dos veces seguidas no
        borra nada la segunda vez.
        """
        all_audios = await self._store.list_recent()
        expired = [a for a in all_audios if a.is_expired(self._retention_days)]
        if not expired:
            return []
        await self._store.delete_many([a.id for a in expired])
        for audio in expired:
            logger.info(
                "Audio '{}' quitado del registro local (expiró en Kie hace {})",
                audio.id,
                -audio.time_left(self._retention_days),
            )
        return expired

    # --- internals ---------------------------------------------------------

    @staticmethod
    def _validate_label(label: str) -> str:
        clean = label.strip()
        if not clean:
            raise AudioValidationError("el label del audio no puede estar vacío")
        if len(clean) > _LABEL_MAX_LENGTH:
            raise AudioValidationError(f"el label del audio supera {_LABEL_MAX_LENGTH} caracteres")
        return clean
