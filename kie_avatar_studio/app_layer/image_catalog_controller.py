"""Facade fina para queries cross-store de imágenes reutilizables.

Combina `UploadedImage` (`ImageStore`, TTL 24h) y `GeneratedImage`
(`GeneratedImageStore`, TTL 14d) en una vista unificada `ImageAssetRef`
usada por:

- El selector de imagen en `NewVideoFormScreen` (la imagen del avatar
  puede provenir de un upload o de una generación).
- El selector de refs en `GenerateImageFormScreen` (las refs del
  `image_input` también pueden mezclar ambos tipos).

Mantenerlo como controller aparte (no como método de
`ImagesController` ni de `GeneratedImagesController`) respeta SRP:
ninguno de los dos stores debe conocer al otro, y este facade
encapsula la lógica de combinación + filtrado por expiración en un
único lugar (CR-3.7).
"""

from __future__ import annotations

from datetime import UTC, datetime

from ..domain.errors import (
    GeneratedImageExpiredError,
    GeneratedImageNotFoundError,
    ImageExpiredError,
    ImageNotFoundError,
)
from ..domain.models import ImageAssetKind, ImageAssetRef
from ..domain.policies import KIE_GENERATED_RETENTION_DAYS, KIE_UPLOAD_RETENTION_HOURS
from ..domain.ports import GeneratedImageStore, ImageStore


class ImageCatalogController:
    """Vista mixta read-only sobre uploaded + generated images."""

    def __init__(
        self,
        uploaded_store: ImageStore,
        generated_store: GeneratedImageStore,
        *,
        upload_retention_hours: int = KIE_UPLOAD_RETENTION_HOURS,
        generated_retention_days: int = KIE_GENERATED_RETENTION_DAYS,
    ) -> None:
        self._uploaded_store = uploaded_store
        self._generated_store = generated_store
        self._upload_retention_hours = upload_retention_hours
        self._generated_retention_days = generated_retention_days

    async def list_usable_assets(self, *, include_expired: bool = False) -> list[ImageAssetRef]:
        """Devuelve uploaded + generated convertidos a `ImageAssetRef`.

        Por defecto filtra los expirados (no son reutilizables). El
        orden es: generated primero (más reciente arriba), luego
        uploaded — pensado para que la UI muestre primero los assets
        con TTL más largo. Si la UI necesita otro orden, ordena después.
        """
        now = datetime.now(UTC)
        refs: list[ImageAssetRef] = []
        for generated in await self._generated_store.list_recent():
            expires_at = generated.expires_at(self._generated_retention_days)
            if not include_expired and generated.is_expired(
                self._generated_retention_days, now=now
            ):
                continue
            refs.append(
                ImageAssetRef(
                    kind=ImageAssetKind.GENERATED,
                    id=generated.id,
                    label=generated.label,
                    kie_url=generated.kie_url,
                    expires_at=expires_at,
                )
            )
        for uploaded in await self._uploaded_store.list_recent():
            expires_at = uploaded.expires_at(self._upload_retention_hours)
            if not include_expired and uploaded.is_expired(self._upload_retention_hours, now=now):
                continue
            refs.append(
                ImageAssetRef(
                    kind=ImageAssetKind.UPLOADED,
                    id=uploaded.id,
                    label=uploaded.label,
                    kie_url=uploaded.kie_url,
                    expires_at=expires_at,
                )
            )
        return refs

    async def resolve_asset(self, kind: ImageAssetKind, asset_id: str) -> ImageAssetRef:
        """Resuelve una `ImageAssetRef` por (kind, id) verificando expiración.

        Único punto de resolución de assets para `VideosController` y
        otros callers que reciben un ref discriminado y necesitan la URL
        actualizada + verificación de TTL. Lanza errores tipados
        correspondientes al `kind` para que el caller distinga la causa.
        """
        if kind == ImageAssetKind.UPLOADED:
            uploaded = await self._uploaded_store.get(asset_id)
            if uploaded is None:
                raise ImageNotFoundError(f"no existe ninguna imagen subida con id={asset_id!r}")
            if uploaded.is_expired(self._upload_retention_hours):
                raise ImageExpiredError(
                    f"la imagen '{uploaded.label}' expiró en Kie hace "
                    f"{-uploaded.time_left(self._upload_retention_hours)}; cargá una nueva."
                )
            return ImageAssetRef(
                kind=ImageAssetKind.UPLOADED,
                id=uploaded.id,
                label=uploaded.label,
                kie_url=uploaded.kie_url,
                expires_at=uploaded.expires_at(self._upload_retention_hours),
            )
        # kind == GENERATED
        generated = await self._generated_store.get(asset_id)
        if generated is None:
            raise GeneratedImageNotFoundError(
                f"no existe ninguna imagen generada con id={asset_id!r}"
            )
        if generated.is_expired(self._generated_retention_days):
            raise GeneratedImageExpiredError(
                f"la imagen '{generated.label}' expiró en Kie hace "
                f"{-generated.time_left(self._generated_retention_days)}; regenerala."
            )
        return ImageAssetRef(
            kind=ImageAssetKind.GENERATED,
            id=generated.id,
            label=generated.label,
            kie_url=generated.kie_url,
            expires_at=generated.expires_at(self._generated_retention_days),
        )
