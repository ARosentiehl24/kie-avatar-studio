"""Tests del cliente HTTP de GitHub Releases (con httpx.MockTransport)."""

from __future__ import annotations

import httpx

from kie_avatar_studio.infra import github_releases


def _patch_transport(monkeypatch, handler) -> None:
    """Reemplaza `httpx.AsyncClient` por uno con `MockTransport(handler)`.

    Capturamos la clase real ANTES del monkeypatch para evitar recursión
    cuando el reemplazo se llama (si reusáramos `httpx.AsyncClient` ahí
    estaríamos llamándonos a nosotros mismos).
    """
    real_async_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def factory(*_args, **kwargs):
        # Sacamos `transport` de kwargs si vino (el módulo no lo pasa).
        kwargs.pop("transport", None)
        return real_async_client(transport=transport, **kwargs)

    monkeypatch.setattr(github_releases.httpx, "AsyncClient", factory)


async def test_returns_release_on_200(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "tag_name": "v1.2.3",
                "html_url": "https://github.com/o/r/releases/tag/v1.2.3",
                "body": "release notes",
                "published_at": "2026-06-05T00:00:00Z",
            },
        )

    _patch_transport(monkeypatch, handler)
    release = await github_releases.get_latest_release("o", "r")
    assert release is not None
    assert release.tag_name == "v1.2.3"
    assert release.html_url == "https://github.com/o/r/releases/tag/v1.2.3"
    assert release.body == "release notes"


async def test_returns_none_on_404(monkeypatch) -> None:
    """Repo sin releases todavía: 404 no es error."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    _patch_transport(monkeypatch, handler)
    assert await github_releases.get_latest_release("o", "r") is None


async def test_returns_none_on_500(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server error")

    _patch_transport(monkeypatch, handler)
    assert await github_releases.get_latest_release("o", "r") is None


async def test_returns_none_on_network_error(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no internet")

    _patch_transport(monkeypatch, handler)
    assert await github_releases.get_latest_release("o", "r") is None


async def test_returns_none_on_invalid_json(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    _patch_transport(monkeypatch, handler)
    assert await github_releases.get_latest_release("o", "r") is None


async def test_returns_none_on_missing_required_fields(monkeypatch) -> None:
    """tag_name y html_url son obligatorios; sin ellos rechazamos."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"body": "x"})

    _patch_transport(monkeypatch, handler)
    assert await github_releases.get_latest_release("o", "r") is None


async def test_handles_missing_optional_fields(monkeypatch) -> None:
    """body y published_at son opcionales."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "tag_name": "v1.0.0",
                "html_url": "https://github.com/o/r/releases/tag/v1.0.0",
            },
        )

    _patch_transport(monkeypatch, handler)
    release = await github_releases.get_latest_release("o", "r")
    assert release is not None
    assert release.body == ""
    assert release.published_at == ""
