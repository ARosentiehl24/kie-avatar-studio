"""Fixtures compartidas para todos los tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest

from kie_avatar_studio.config import Settings
from kie_avatar_studio.infra.db import JobsDB
from kie_avatar_studio.infra.kie_client import KieClient


@pytest.fixture
def tmp_settings(tmp_path: Path) -> Settings:
    """Settings aisladas en tmp_path, con todos los directorios ya creados."""
    settings = Settings(
        kie_api_key="test-key",
        data_dir=tmp_path / "data",
        outputs_dir=tmp_path / "outputs",
        inputs_dir=tmp_path / "inputs",
        presets_dir=tmp_path / "presets",
        batch_jobs_dir=tmp_path / "batch_jobs",
        logs_dir=tmp_path / "logs",
    )
    settings.ensure_dirs()
    return settings


@pytest.fixture
async def jobs_db(tmp_settings: Settings) -> AsyncIterator[JobsDB]:
    db = JobsDB(tmp_settings.db_path)
    await db.init()
    yield db


@pytest.fixture
def mock_transport_factory() -> Iterator[callable]:
    """Devuelve una factory que construye un `MockTransport` desde un handler."""

    def _factory(handler: callable) -> httpx.MockTransport:
        return httpx.MockTransport(handler)

    yield _factory


@pytest.fixture
async def mock_kie_client(
    tmp_settings: Settings, mock_transport_factory: callable
) -> AsyncIterator[tuple[KieClient, list[httpx.Request]]]:
    """KieClient con transporte mockeado. El handler se reasigna en cada test."""
    captured: list[httpx.Request] = []

    def default_handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"data": {}})

    client = KieClient(tmp_settings)
    await client.aclose()
    client._client = httpx.AsyncClient(
        transport=mock_transport_factory(default_handler),
        headers={"Authorization": f"Bearer {tmp_settings.kie_api_key}"},
    )
    try:
        yield client, captured
    finally:
        await client.aclose()
