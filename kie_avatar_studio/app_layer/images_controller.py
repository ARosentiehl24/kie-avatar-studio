"""Controller para administrar imágenes subidas a Kie.

Depende solo de los `Protocol` del dominio:
- `ImageStore` para persistencia local.
- `KieGateway` para subir el archivo a Kie y obtener su URL.

Cada `upload_image` hace:
1. Validar el path local (formato, tamaño, existencia) con `policies`.
2. Subir vía `KieGateway.upload_file` (HTTP puro, retries 5xx automáticos).
3. Persistir el `UploadedImage` con la URL devuelta + metadata local.
4. Devolver el registro creado para que la UI lo refresque.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Final

from loguru import logger

from ..domain.errors import ImageExpiredError, ImageNotFoundError, ImageValidationError
from ..domain.models import UploadedImage
from ..domain.policies import KIE_UPLOAD_RETENTION_HOURS, validate_image_path
from ..domain.ports import ImageStore, KieGateway
from .ids import sanitize_filename

DEFAULT_UPLOAD_FOLDER: Final[str] = "images/avatar-models"


class ImagesController:
    """Casos de uso sobre `UploadedImage`. Sin estado."""

    def __init__(
        self,
        store: ImageStore,
        gateway: KieGateway,
        upload_folder: str = DEFAULT_UPLOAD_FOLDER,
        retention_hours: int = KIE_UPLOAD_RETENTION_HOURS,
    ) -> None:
        self._store = store
        self._gateway = gateway
        self._upload_folder = upload_folder
        self._retention_hours = retention_hours

    @property
    def retention_hours(self) -> int:
        return self._retention_hours

    async def list_uploaded(self) -> list[UploadedImage]:
        return await self._store.list_recent()

    async def get(self, image_id: str) -> UploadedImage | None:
        return await self._store.get(image_id)

    async def get_for_use(self, image_id: str) -> UploadedImage:
        """Devuelve la imagen lista para reutilizar en un job.

        Lanza:
        - `ImageNotFoundError` si no existe en el store local.
        - `ImageExpiredError` si ya superó la ventana de retención de Kie
          (el archivo en `kie_url` ya fue auto-borrado por el proveedor).
          Para imágenes subidas via File Upload API, Kie limita a 24h.

        Pensado para que cualquier capa que vaya a referenciar una
        `UploadedImage` por id (ej. `JobRunner` al armar un job) falle
        pronto y con un error claro en lugar de pegar contra un 404 de
        Kie en runtime (que se reporta como "Image fetch failed").
        """
        image = await self._store.get(image_id)
        if image is None:
            raise ImageNotFoundError(f"no existe ninguna imagen con id={image_id!r}")
        if image.is_expired(self._retention_hours):
            raise ImageExpiredError(
                f"la imagen '{image.label}' expiró en Kie hace "
                f"{-image.time_left(self._retention_hours)}; cargá una nueva."
            )
        return image

    async def upload(
        self,
        local_path: Path,
        label: str,
    ) -> UploadedImage:
        """Sube `local_path` a Kie y persiste el registro local.

        El `id` se deriva del label sanitizado para que sea estable y legible
        en el filesystem (igual criterio que `KeysController`). Si ya existe
        una imagen con ese id, el upsert reemplaza el registro previo.
        """
        if not label.strip():
            raise ImageValidationError("el label no puede estar vacío")
        validate_image_path(local_path)
        # `resolve()` toca el filesystem; lo movemos a un thread para no
        # bloquear la event loop con symlinks o paths de red.
        resolved_path = await asyncio.to_thread(local_path.resolve)
        result = await self._gateway.upload_file(
            local_path,
            upload_path=self._upload_folder,
        )
        image_id = sanitize_filename(label.strip()).lower()
        image = UploadedImage(
            id=image_id,
            label=label.strip(),
            local_path=str(resolved_path),
            kie_url=result.download_url,
            kie_file_path=result.file_path,
            file_size=result.file_size,
            mime_type=result.mime_type,
        )
        await self._store.upsert(image)
        return image

    async def delete(self, image_id: str) -> None:
        await self._store.delete(image_id)

    async def cleanup_expired(self) -> list[UploadedImage]:
        """Borra de la DB local todas las imágenes cuyo TTL ya venció en Kie.

        Devuelve la lista de imágenes quitadas para que el caller pueda
        notificar/loguear. Idempotente: llamarla dos veces seguidas no
        borra nada la segunda vez.
        """
        all_images = await self._store.list_recent()
        expired = [img for img in all_images if img.is_expired(self._retention_hours)]
        for image in expired:
            await self._store.delete(image.id)
            logger.info(
                "Imagen '{}' quitada del registro local (expiró en Kie hace {})",
                image.id,
                -image.time_left(self._retention_hours),
            )
        return expired
