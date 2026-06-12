"""Controller que coordina el `KeyStore` con la validación remota contra Kie.

Pura orquestación: depende solo de los `Protocol` del dominio (KeyStore,
KieGateway), nunca de las implementaciones concretas (CR-2.5 DIP).

Política de validación (`test_key`):
- Consulta `GET /api/v1/chat/credit` con la key a validar. Este endpoint
  confirma autenticación y devuelve saldo; `recordInfo` con task inexistente
  devuelve `code:422 recordInfo is null`, así que no sirve como probe estable.
- El gateway se construye **por key** (no usa el global) porque la key activa de
  la app puede ser distinta a la que estamos validando.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from loguru import logger

from ..domain.errors import (
    KeyNotFoundError,
    KeyValidationError,
    KieClientError,
)
from ..domain.models import KeyValidationStatus, KieKey
from ..domain.policies import validate_key_label, validate_kie_key
from ..domain.ports import KeyStore, KieGateway

GatewayFactory = Callable[[str], KieGateway]


class KeysController:
    """Casos de uso sobre `KieKey`. Sin estado: cada llamada va al store."""

    def __init__(self, store: KeyStore, gateway_factory: GatewayFactory) -> None:
        self._store = store
        self._gateway_factory = gateway_factory

    async def list_keys(self) -> list[KieKey]:
        return await self._store.load()

    async def add_key(self, key_id: str, label: str, key: str) -> KieKey:
        validate_key_label(label)
        validate_kie_key(key)
        if await self._store.get(key_id) is not None:
            raise KeyValidationError(f"ya existe una key con id={key_id!r}")
        kie_key = KieKey(id=key_id, label=label.strip(), key=key)
        await self._store.upsert(kie_key)
        return kie_key

    async def rename_key(self, key_id: str, new_label: str) -> KieKey:
        validate_key_label(new_label)
        existing = await self._require(key_id)
        updated = existing.model_copy(update={"label": new_label.strip()})
        await self._store.upsert(updated)
        return updated

    async def delete_key(self, key_id: str) -> None:
        await self._store.delete(key_id)

    async def set_active(self, key_id: str | None) -> None:
        if key_id is not None:
            await self._require(key_id)
        await self._store.set_active(key_id)

    async def get_active(self) -> KieKey | None:
        return await self._store.get_active()

    async def test_key(self, key_id: str) -> KieKey:
        """Llama a Kie con esta key y persiste el resultado en metadata.

        Consulta saldo (`get_account_credits` — gratis) como prueba de
        autenticación. Si falla por 401/403 se marca `"unauthorized"`; otros
        errores quedan como `"error"`.
        """
        key = await self._require(key_id)
        status, credits = await self._probe_and_check_balance(key.key)
        updated = key.model_copy(
            update={
                "last_validated_at": datetime.now(UTC),
                "last_validated_status": status,
                "last_known_credits": (credits if credits is not None else key.last_known_credits),
            }
        )
        await self._store.upsert(updated)
        return updated

    # --- helpers -----------------------------------------------------------

    async def _require(self, key_id: str) -> KieKey:
        key = await self._store.get(key_id)
        if key is None:
            raise KeyNotFoundError(f"no existe ninguna key con id={key_id!r}")
        return key

    async def _probe_and_check_balance(
        self, secret: str
    ) -> tuple[KeyValidationStatus, float | None]:
        """Devuelve `(status, balance)`: balance solo si status == 'ok'."""
        gateway = self._gateway_factory(secret)
        try:
            try:
                balance = await gateway.get_account_credits()
            except KieClientError as exc:
                return _classify_client_error(exc), None
            except Exception:
                logger.exception("Probe de key falló por un error no clasificado")
                return "error", None
            return "ok", balance
        finally:
            await gateway.aclose()


def _classify_client_error(exc: KieClientError) -> KeyValidationStatus:
    message = str(exc)
    if "HTTP 401" in message or "HTTP 403" in message:
        return "unauthorized"
    return "error"
