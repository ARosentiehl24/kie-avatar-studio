"""Smoke test de `ImagesScreen` y su integración con `ImagesController`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from textual.widgets import Button, DataTable

from kie_avatar_studio.app import KieAvatarStudioApp
from kie_avatar_studio.config import Settings
from kie_avatar_studio.domain.models import UploadedImage


def _build_app(tmp_path: Path) -> KieAvatarStudioApp:
    settings = Settings(
        kie_api_key="test-key",
        data_dir=tmp_path / "data",
        outputs_dir=tmp_path / "outputs",
        inputs_dir=tmp_path / "inputs",
        presets_dir=tmp_path / "presets",
        logs_dir=tmp_path / "logs",
    )
    settings.ensure_dirs()
    app = KieAvatarStudioApp(settings=settings)

    # Stub del indicador de saldo: ver doc en test_audios_screen.py:_build_app.
    async def fake_check() -> float | None:
        return None

    app._check_credits = fake_check  # type: ignore[method-assign]
    return app


def _swap_mock_kie(app: KieAvatarStudioApp, handler) -> None:
    """Reemplaza el transport interno del KieClient activo + el del controller."""
    app.kie._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": f"Bearer {app.settings.kie_api_key}"},
    )


def _png(tmp_path: Path) -> Path:
    p = tmp_path / "img.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    return p


async def test_images_screen_opens_with_i_hotkey(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.pause()
        await pilot.press("i")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "ImagesScreen"


async def test_images_screen_lists_uploaded_images(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.pause()
        # Sembramos directamente vía controller para no depender del modal.
        _swap_mock_kie(
            app,
            lambda r: httpx.Response(
                200,
                json={
                    "data": {
                        "fileName": "img.png",
                        "filePath": "kieai/img.png",
                        "downloadUrl": "https://tempfile.redpandaai.co/img.png",
                        "fileSize": 108,
                        "mimeType": "image/png",
                    }
                },
            ),
        )
        await app.images_controller.upload(_png(tmp_path), "modelo principal")

        await pilot.press("i")
        await pilot.pause()
        table = app.screen.query_one("#images-table", DataTable)
        assert table.row_count == 1


async def test_images_screen_buttons_render_with_labels(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        await pilot.press("i")
        await pilot.pause()
        for btn_id, expected in (
            ("img-upload", "Cargar"),
            ("img-view", "Ver"),
            ("img-copy-url", "Copiar URL"),
            ("img-delete", "Quitar"),
        ):
            btn = app.screen.query_one(f"#{btn_id}", Button)
            assert str(btn.label) == expected
            assert btn.region.width >= max(len(expected) + 2, 9)


# --- _handle_view: 3 caminos (local OK / URL fallback / expirada) ---------


async def test_handle_view_opens_local_when_file_exists(tmp_path: Path) -> None:
    """Si el archivo local existe, debe abrirse con el visor local."""
    app = _build_app(tmp_path)
    local_calls: list[Path] = []
    url_calls: list[str] = []

    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.pause()
        local_file = _png(tmp_path)
        _swap_mock_kie(
            app,
            lambda r: httpx.Response(
                200,
                json={
                    "data": {
                        "fileName": "img.png",
                        "filePath": "kieai/img.png",
                        "downloadUrl": "https://x/img.png",
                        "fileSize": 108,
                        "mimeType": "image/png",
                    }
                },
            ),
        )
        await app.images_controller.upload(local_file, "modelo")

        await pilot.press("i")
        await pilot.pause()
        # inyectamos openers fake en la pantalla
        screen = app.screen

        async def fake_local(p: Path) -> None:
            local_calls.append(p)

        async def fake_url(u: str) -> None:
            url_calls.append(u)

        screen._open_local_path = fake_local  # type: ignore[attr-defined]
        screen._open_url = fake_url  # type: ignore[attr-defined]
        await pilot.click("#img-view")
        await pilot.pause()

    assert len(local_calls) == 1
    assert local_calls[0] == local_file.resolve()
    assert url_calls == []  # no se intentó la URL


async def test_handle_view_falls_back_to_url_when_local_missing(tmp_path: Path) -> None:
    """Si el archivo local desapareció pero la URL Kie sigue vigente, abre browser."""
    app = _build_app(tmp_path)
    local_calls: list[Path] = []
    url_calls: list[str] = []

    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.pause()
        # Sembramos directo en la DB con un local_path que NO existe.
        image = UploadedImage(
            id="modelo",
            label="modelo",
            local_path="/tmp/no-existe-jamas.png",
            kie_url="https://tempfile.redpandaai.co/kieai/abc/modelo.png",
            kie_file_path="kieai/abc/modelo.png",
            file_size=108,
            mime_type="image/png",
            uploaded_at=datetime.now(UTC),
        )
        await app.images_db.upsert(image)

        await pilot.press("i")
        await pilot.pause()
        screen = app.screen

        async def fake_local(p: Path) -> None:
            local_calls.append(p)

        async def fake_url(u: str) -> None:
            url_calls.append(u)

        screen._open_local_path = fake_local  # type: ignore[attr-defined]
        screen._open_url = fake_url  # type: ignore[attr-defined]
        await pilot.click("#img-view")
        await pilot.pause()

    assert local_calls == []
    assert url_calls == ["https://tempfile.redpandaai.co/kieai/abc/modelo.png"]


async def test_handle_view_rejects_expired_image(tmp_path: Path) -> None:
    """Si la imagen ya expiró, ni se intenta abrir local ni URL."""
    app = _build_app(tmp_path)
    local_calls: list[Path] = []
    url_calls: list[str] = []

    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.pause()
        old = UploadedImage(
            id="vieja",
            label="vieja",
            local_path="/tmp/x.png",
            kie_url="https://x",
            kie_file_path="x",
            file_size=1,
            mime_type="image/png",
            uploaded_at=datetime.now(UTC) - timedelta(days=20),
        )
        await app.images_db.upsert(old)

        await pilot.press("i")
        await pilot.pause()
        screen = app.screen

        async def fake_local(p: Path) -> None:
            local_calls.append(p)

        async def fake_url(u: str) -> None:
            url_calls.append(u)

        screen._open_local_path = fake_local  # type: ignore[attr-defined]
        screen._open_url = fake_url  # type: ignore[attr-defined]
        await pilot.click("#img-view")
        await pilot.pause()

    assert local_calls == []
    assert url_calls == []


async def test_table_does_not_render_clickable_url(tmp_path: Path) -> None:
    """Regresión: la columna de la tabla no debe contener 'https://' ni el
    ellipsis '…' que Textual transformaría en links clickeables inválidos
    al truncar la URL (bug reportado: clic abría .../%E2%80%A6).
    """
    app = _build_app(tmp_path)
    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.pause()
        image = UploadedImage(
            id="modelo",
            label="modelo",
            local_path="/tmp/x.png",
            kie_url="https://tempfile.redpandaai.co/kieai/41abcdefghijklmnop/modelo.png",
            kie_file_path="kieai/41abcdefghijklmnop/modelo.png",
            file_size=108,
            mime_type="image/png",
            uploaded_at=datetime.now(UTC),
        )
        await app.images_db.upsert(image)
        await pilot.press("i")
        await pilot.pause()
        table = app.screen.query_one("#images-table", DataTable)
        # row_count > 0
        assert table.row_count == 1
        # Recorremos todas las celdas y verificamos que ninguna empieza con
        # https:// (Textual no genera link auto si no detecta el esquema).
        for col_index in range(len(table.columns)):
            value = str(table.get_cell_at((0, col_index)))
            assert "https://" not in value, f"columna {col_index} contiene URL: {value!r}"
            assert "%E2%80%A6" not in value
