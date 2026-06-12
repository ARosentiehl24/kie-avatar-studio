import httpx
import pytest

from kie_avatar_studio.app_layer.keys_controller import KeysController
from kie_avatar_studio.domain.errors import KeyNotFoundError, KeyValidationError
from kie_avatar_studio.infra.keys_store import KeysStore
from kie_avatar_studio.infra.kie_client import KieClient


def _build_mocked_client(tmp_settings, handler) -> KieClient:
    """Construye un KieClient con MockTransport sin tocar la network real.

    Reemplazamos el `httpx.AsyncClient` interno antes de cualquier llamada,
    así no hay que hacer `aclose` previo (que requería un event loop activo
    y rompía en Python 3.13).
    """
    client = KieClient(tmp_settings)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": f"Bearer {tmp_settings.kie_api_key}"},
    )
    return client


@pytest.fixture
async def store(tmp_path) -> KeysStore:
    s = KeysStore(tmp_path / "keys.json")
    await s.init()
    return s


async def test_add_key_persists_and_returns(store: KeysStore, tmp_settings) -> None:
    def _factory(_secret: str):
        return _build_mocked_client(tmp_settings, lambda r: httpx.Response(200))

    ctl = KeysController(store, _factory)
    key = await ctl.add_key("dev", "Cuenta dev", "sk-12345678")
    assert key.id == "dev"
    listed = await ctl.list_keys()
    assert len(listed) == 1


async def test_add_key_duplicate_id_raises(store: KeysStore, tmp_settings) -> None:
    ctl = KeysController(
        store,
        lambda _s: _build_mocked_client(tmp_settings, lambda r: httpx.Response(200)),
    )
    await ctl.add_key("dev", "Cuenta dev", "sk-12345678")
    with pytest.raises(KeyValidationError, match="ya existe"):
        await ctl.add_key("dev", "Otra", "sk-87654321")


async def test_rename_key(store: KeysStore, tmp_settings) -> None:
    ctl = KeysController(
        store,
        lambda _s: _build_mocked_client(tmp_settings, lambda r: httpx.Response(200)),
    )
    await ctl.add_key("dev", "viejo", "sk-12345678")
    renamed = await ctl.rename_key("dev", "nuevo")
    assert renamed.label == "nuevo"


async def test_set_active_missing_raises(store: KeysStore, tmp_settings) -> None:
    ctl = KeysController(
        store,
        lambda _s: _build_mocked_client(tmp_settings, lambda r: httpx.Response(200)),
    )
    with pytest.raises(KeyNotFoundError):
        await ctl.set_active("ghost")


async def test_test_key_ok_marks_validated(store: KeysStore, tmp_settings) -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 200, "data": 123.45})

    ctl = KeysController(store, lambda _s: _build_mocked_client(tmp_settings, handler))
    await ctl.add_key("dev", "dev", "sk-12345678")
    tested = await ctl.test_key("dev")
    assert tested.last_validated_status == "ok"
    assert tested.last_known_credits == 123.45
    assert tested.last_validated_at is not None


async def test_test_key_unauthorized_marks_401(store: KeysStore, tmp_settings) -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    ctl = KeysController(store, lambda _s: _build_mocked_client(tmp_settings, handler))
    await ctl.add_key("dev", "dev", "sk-12345678")
    tested = await ctl.test_key("dev")
    assert tested.last_validated_status == "unauthorized"


async def test_test_key_server_error_marks_error(store: KeysStore, tmp_settings) -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    ctl = KeysController(store, lambda _s: _build_mocked_client(tmp_settings, handler))
    await ctl.add_key("dev", "dev", "sk-12345678")
    tested = await ctl.test_key("dev")
    assert tested.last_validated_status == "error"


async def test_test_key_missing_id_raises(store: KeysStore, tmp_settings) -> None:
    ctl = KeysController(
        store,
        lambda _s: _build_mocked_client(tmp_settings, lambda r: httpx.Response(200)),
    )
    with pytest.raises(KeyNotFoundError):
        await ctl.test_key("ghost")
