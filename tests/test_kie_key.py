from datetime import datetime

from kie_avatar_studio.domain.models import KieKey


def test_default_fields() -> None:
    key = KieKey(id="dev", label="Cuenta dev", key="sk-1234567890")
    assert key.id == "dev"
    assert key.label == "Cuenta dev"
    assert key.last_validated_at is None
    assert key.last_validated_status is None
    assert isinstance(key.created_at, datetime)


def test_masked_hides_all_but_tail() -> None:
    key = KieKey(id="x", label="x", key="sk-abcdefghij")
    masked = key.masked()
    assert masked.endswith("ghij")
    assert masked[0] == "*"
    assert len(masked) == len("sk-abcdefghij")
    assert masked.count("*") == len("sk-abcdefghij") - 4


def test_masked_when_key_shorter_than_tail() -> None:
    key = KieKey(id="x", label="x", key="abcd")
    assert key.masked(visible_tail=8) == "****"


def test_serialization_roundtrip() -> None:
    original = KieKey(id="dev", label="Cuenta dev", key="sk-1234567890abcd")
    payload = original.model_dump_json()
    restored = KieKey.model_validate_json(payload)
    assert restored == original
