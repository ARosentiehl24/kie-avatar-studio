"""Factory para construir runners hoja de imagen ad-hoc.

El `WorkflowStepRunner` y `WorkflowBaseResolver` necesitan instanciar
runners hoja con dependencias compartidas. Esta capa centraliza la
construcción evitando los 3 sitios de duplicación detectados por el
code-quality-reviewer (CR-3.7).

`ImageJobRunner` no necesita parametrización pero usa la misma factory
para que el caller dependa de UNA sola abstracción.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Settings
from ..domain.ports import (
    GeneratedImageStore,
    ImageJobRepository,
    ImageStore,
    KieGateway,
)
from .image_job_runner import ImageJobRunner


@dataclass(frozen=True, slots=True)
class ImageRunnerDeps:
    """Dependencias inmutables compartidas para construir `ImageJobRunner`."""

    settings: Settings
    client: KieGateway
    image_jobs_repo: ImageJobRepository
    generated_images_store: GeneratedImageStore
    uploaded_images_store: ImageStore


class WorkflowRunnerFactory:
    """Crea runners hoja con dependencias pre-bound desde el composition root."""

    def __init__(self, image_deps: ImageRunnerDeps) -> None:
        self._image_deps = image_deps

    def make_image_runner(self) -> ImageJobRunner:
        return ImageJobRunner(
            self._image_deps.settings,
            self._image_deps.client,
            self._image_deps.image_jobs_repo,
            self._image_deps.generated_images_store,
            self._image_deps.uploaded_images_store,
        )


__all__ = [
    "ImageRunnerDeps",
    "WorkflowRunnerFactory",
]
