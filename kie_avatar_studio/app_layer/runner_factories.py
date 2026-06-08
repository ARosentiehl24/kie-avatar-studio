"""Factories para construir runners hoja (ImageJobRunner, AudioJobRunner) ad-hoc.

El `WorkflowStepRunner` y `WorkflowBaseResolver` necesitan instanciar
runners hoja con dependencias compartidas. Esta capa centraliza la
construcción evitando los 3 sitios de duplicación detectados por el
code-quality-reviewer (CR-3.7).

`AudioJobRunner` requiere parametrización dinámica (`tts_model` puede
cambiar por workflow según `audio_language`), por eso una factory en
vez de inyectar la instancia ya construida.

`ImageJobRunner` no necesita parametrización pero usa la misma factory
para que el caller dependa de UNA sola abstracción.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Settings
from ..domain.ports import (
    AudioJobRepository,
    AudioStore,
    GeneratedImageStore,
    ImageJobRepository,
    ImageStore,
    KieGateway,
)
from .audio_job_runner import AudioJobRunner
from .image_job_runner import ImageJobRunner


@dataclass(frozen=True, slots=True)
class ImageRunnerDeps:
    """Dependencias inmutables compartidas para construir `ImageJobRunner`."""

    settings: Settings
    client: KieGateway
    image_jobs_repo: ImageJobRepository
    generated_images_store: GeneratedImageStore
    uploaded_images_store: ImageStore


@dataclass(frozen=True, slots=True)
class AudioRunnerDeps:
    """Dependencias inmutables compartidas para construir `AudioJobRunner`."""

    settings: Settings
    client: KieGateway
    audio_jobs_repo: AudioJobRepository
    audios_store: AudioStore


class WorkflowRunnerFactory:
    """Crea runners hoja con dependencias pre-bound desde el composition root."""

    def __init__(
        self,
        image_deps: ImageRunnerDeps,
        audio_deps: AudioRunnerDeps,
    ) -> None:
        self._image_deps = image_deps
        self._audio_deps = audio_deps

    def make_image_runner(self) -> ImageJobRunner:
        return ImageJobRunner(
            self._image_deps.settings,
            self._image_deps.client,
            self._image_deps.image_jobs_repo,
            self._image_deps.generated_images_store,
            self._image_deps.uploaded_images_store,
        )

    def make_audio_runner(self, *, tts_model: str | None) -> AudioJobRunner:
        return AudioJobRunner(
            self._audio_deps.settings,
            self._audio_deps.client,
            self._audio_deps.audio_jobs_repo,
            self._audio_deps.audios_store,
            tts_model=tts_model,
        )


__all__ = [
    "AudioRunnerDeps",
    "ImageRunnerDeps",
    "WorkflowRunnerFactory",
]
