from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

from kie_avatar_studio.domain.errors import KeyNotFoundError
from kie_avatar_studio.domain.models import KieKey
from kie_avatar_studio.infra.keys_store import KEYS_FILE_NAME, KeysStore

# `chmod 0o600` solo es significativo en Unix-like. Windows NTFS no
# expone bits POSIX vía `os.chmod` — siempre devuelve `0o666` para
# archivos legibles. La seguridad real en Windows pasa por ACLs y eso
# no se testea acá. Skip los tests de permisos en esa plataforma.
_REQUIRES_POSIX = pytest.mark.skipif(
    sys.platform == "win32",
    reason="chmod 0o600 no aplica en Windows NTFS (devuelve siempre 0o666)",
)


@pytest.fixture
async def store(tmp_path: Path) -> KeysStore:
    s = KeysStore(tmp_path / KEYS_FILE_NAME)
    await s.init()
    return s


def _key(key_id: str = "dev", label: str = "Cuenta dev", secret: str = "sk-abcdefgh") -> KieKey:
    return KieKey(id=key_id, label=label, key=secret)


async def test_init_creates_empty_file(tmp_path: Path) -> None:
    s = KeysStore(tmp_path / KEYS_FILE_NAME)
    await s.init()
    assert (tmp_path / KEYS_FILE_NAME).exists()
    payload = json.loads((tmp_path / KEYS_FILE_NAME).read_text())
    assert payload == {"active_key_id": None, "keys": []}


@_REQUIRES_POSIX
async def test_init_applies_0600(tmp_path: Path) -> None:
    s = KeysStore(tmp_path / KEYS_FILE_NAME)
    await s.init()
    mode = (tmp_path / KEYS_FILE_NAME).stat().st_mode & 0o777
    assert mode == 0o600


async def test_upsert_and_get(store: KeysStore) -> None:
    await store.upsert(_key())
    fetched = await store.get("dev")
    assert fetched is not None
    assert fetched.label == "Cuenta dev"


async def test_upsert_roundtrips_last_known_credits(store: KeysStore) -> None:
    key = _key()
    key.last_known_credits = 123.45
    await store.upsert(key)
    fetched = await store.get("dev")
    assert fetched is not None
    assert fetched.last_known_credits == 123.45


async def test_upsert_updates_existing(store: KeysStore) -> None:
    await store.upsert(_key(label="vieja"))
    await store.upsert(_key(label="nueva"))
    fetched = await store.get("dev")
    assert fetched is not None
    assert fetched.label == "nueva"
    assert len(await store.load()) == 1


async def test_delete_existing(store: KeysStore) -> None:
    await store.upsert(_key())
    await store.delete("dev")
    assert await store.get("dev") is None


async def test_delete_missing_raises(store: KeysStore) -> None:
    with pytest.raises(KeyNotFoundError):
        await store.delete("no-existe")


async def test_set_active_and_get_active(store: KeysStore) -> None:
    await store.upsert(_key("dev"))
    await store.upsert(_key("prod", "Prod", "sk-prodprod"))
    await store.set_active("prod")
    active = await store.get_active()
    assert active is not None
    assert active.id == "prod"


async def test_set_active_invalid_id_raises(store: KeysStore) -> None:
    with pytest.raises(KeyNotFoundError):
        await store.set_active("ghost")


async def test_set_active_none_clears(store: KeysStore) -> None:
    await store.upsert(_key())
    await store.set_active("dev")
    await store.set_active(None)
    assert await store.get_active() is None


async def test_delete_clears_active_pointer(store: KeysStore) -> None:
    await store.upsert(_key())
    await store.set_active("dev")
    await store.delete("dev")
    assert await store.get_active() is None


async def test_atomic_write_preserves_old_on_garbled_input(tmp_path: Path) -> None:
    """Si el JSON en disco es inválido, `load` devuelve vacío sin crashear."""
    path = tmp_path / KEYS_FILE_NAME
    s = KeysStore(path)
    await s.init()
    path.write_text("{ esto NO es json", encoding="utf-8")
    assert await s.load() == []
    assert await s.get_active() is None


async def test_active_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / KEYS_FILE_NAME
    s1 = KeysStore(path)
    await s1.init()
    await s1.upsert(_key())
    await s1.set_active("dev")

    s2 = KeysStore(path)
    active = await s2.get_active()
    assert active is not None
    assert active.id == "dev"


async def test_elevenlabs_api_key_roundtrips_in_integrations(store: KeysStore) -> None:
    assert await store.get_elevenlabs_api_key() is None
    await store.set_elevenlabs_api_key("  sk-elevenlabs  ")
    assert await store.get_elevenlabs_api_key() == "sk-elevenlabs"


async def test_elevenlabs_api_key_can_be_cleared(store: KeysStore) -> None:
    await store.set_elevenlabs_api_key("sk-elevenlabs")
    await store.set_elevenlabs_api_key("  ")
    assert await store.get_elevenlabs_api_key() == ""


@_REQUIRES_POSIX
async def test_chmod_0600_preserved_after_upsert(tmp_path: Path) -> None:
    path = tmp_path / KEYS_FILE_NAME
    s = KeysStore(path)
    await s.init()
    await s.upsert(_key())
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600
