"""Tests para `infra.audio_downloader.download_audio` — descarga atómica."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from kie_avatar_studio.infra import audio_downloader
from kie_avatar_studio.infra.audio_downloader import download_audio

_FAKE_MP3 = b"ID3\x03\x00\x00\x00" + b"\x00" * 200


def _patch_async_client(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """Reemplaza `httpx.AsyncClient` del módulo por uno con MockTransport."""
    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def factory(*_args, **kwargs):
        kwargs.pop("transport", None)
        return original(transport=transport, **kwargs)

    monkeypatch.setattr(audio_downloader.httpx, "AsyncClient", factory)


async def test_download_audio_writes_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_FAKE_MP3, headers={"Content-Type": "audio/mpeg"})

    _patch_async_client(monkeypatch, handler)
    dest = tmp_path / "voice.mp3"

    await download_audio("https://x.com/voice.mp3", dest)

    assert dest.exists()
    assert dest.read_bytes() == _FAKE_MP3


async def test_download_audio_creates_parent_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_FAKE_MP3)

    _patch_async_client(monkeypatch, handler)
    nested_dest = tmp_path / "a" / "b" / "voice.mp3"

    await download_audio("https://x.com/voice.mp3", nested_dest)

    assert nested_dest.exists()


async def test_download_audio_atomic_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_FAKE_MP3)

    _patch_async_client(monkeypatch, handler)
    dest = tmp_path / "voice.mp3"

    await download_audio("https://x.com/voice.mp3", dest)

    # No queda residuo del `.part`.
    assert dest.exists()
    assert not (tmp_path / "voice.mp3.part").exists()


async def test_download_audio_raises_oserror_on_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"not found")

    _patch_async_client(monkeypatch, handler)
    dest = tmp_path / "voice.mp3"

    with pytest.raises(OSError, match="no pude descargar"):
        await download_audio("https://x.com/no.mp3", dest)

    # Sin archivos parciales tras el fallo.
    assert not dest.exists()
    assert not (tmp_path / "voice.mp3.part").exists()


async def test_download_audio_raises_oserror_on_connection_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    _patch_async_client(monkeypatch, handler)
    dest = tmp_path / "voice.mp3"

    with pytest.raises(OSError, match="no pude descargar"):
        await download_audio("https://x.com/voice.mp3", dest)
