"""Smoke test del `ImageFilePickerScreen` y su integración con upload."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import Button, DirectoryTree, Input

from kie_avatar_studio.app import KieAvatarStudioApp
from kie_avatar_studio.config import Settings
from kie_avatar_studio.ui.screens.file_picker import (
    ImageFilePickerScreen,
    _ImagesDirectoryTree,
)


def _build_app(tmp_path: Path) -> KieAvatarStudioApp:
    settings = Settings(
        kie_api_key="",
        data_dir=tmp_path / "data",
        outputs_dir=tmp_path / "outputs",
        inputs_dir=tmp_path / "inputs",
        presets_dir=tmp_path / "presets",
        logs_dir=tmp_path / "logs",
    )
    settings.ensure_dirs()
    return KieAvatarStudioApp(settings=settings)


def _seed_images(root: Path) -> None:
    (root / "modelo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    (root / "foto.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
    (root / "ignorame.txt").write_text("este archivo no debe aparecer")
    (root / ".oculto.png").write_bytes(b"hidden")
    subdir = root / "subcarpeta"
    subdir.mkdir()
    (subdir / "interior.png").write_bytes(b"\x89PNG")


def test_images_directory_tree_filters_only_images(tmp_path: Path) -> None:
    _seed_images(tmp_path)
    tree = _ImagesDirectoryTree(str(tmp_path))
    visible = {p.name for p in tree.filter_paths(tmp_path.iterdir())}
    assert "modelo.png" in visible
    assert "foto.jpg" in visible
    assert "subcarpeta" in visible
    assert "ignorame.txt" not in visible
    assert ".oculto.png" not in visible


@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
async def test_picker_opens_from_upload_modal(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.pause()
        await pilot.press("i")
        await pilot.pause()
        await pilot.click("#img-upload")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "UploadImageFormScreen"
        await pilot.click("#browse")
        # Damos tiempo al DirectoryTree para que su watcher de filesystem
        # arranque sin dejar coroutines pendientes (warning con error=true).
        await pilot.pause()
        await pilot.pause()
        assert app.screen.__class__.__name__ == "ImageFilePickerScreen"
        tree = app.screen.query_one("#file-picker-tree", DirectoryTree)
        assert tree.has_focus
        # cancelar con escape vuelve al modal de upload
        await pilot.press("escape")
        await pilot.pause()
        await pilot.pause()
        assert app.screen.__class__.__name__ == "UploadImageFormScreen"


async def test_picker_dismiss_with_path_fills_input(tmp_path: Path) -> None:
    """Verifica que el callback `_on_file_picked` rellena el Input cuando el
    usuario elige un archivo. Probamos llamando al dismiss directo del modal
    porque la navegación del DirectoryTree en Pilot es flaky en archivos
    creados al vuelo.
    """
    _seed_images(tmp_path)
    app = _build_app(tmp_path)
    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.pause()
        await pilot.press("i")
        await pilot.pause()
        await pilot.click("#img-upload")
        await pilot.pause()
        upload_screen = app.screen  # UploadImageFormScreen
        chosen = tmp_path / "modelo.png"
        upload_screen._on_file_picked(chosen)  # type: ignore[attr-defined]
        await pilot.pause()
        input_widget = upload_screen.query_one("#image-path", Input)
        assert input_widget.value == str(chosen)


async def test_picker_dismiss_with_none_keeps_input_empty(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.pause()
        await pilot.press("i")
        await pilot.pause()
        await pilot.click("#img-upload")
        await pilot.pause()
        upload_screen = app.screen
        upload_screen._on_file_picked(None)  # type: ignore[attr-defined]
        await pilot.pause()
        input_widget = upload_screen.query_one("#image-path", Input)
        assert input_widget.value == ""


async def test_picker_buttons_render(tmp_path: Path) -> None:
    """Botones del picker se montan con sus labels visibles."""
    _seed_images(tmp_path)
    picker = ImageFilePickerScreen(start_path=tmp_path)
    app = _build_app(tmp_path)
    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.pause()
        await app.push_screen(picker)
        await pilot.pause()
        cancel = app.screen.query_one("#cancel", Button)
        confirm = app.screen.query_one("#confirm", Button)
        assert str(cancel.label) == "Cancelar"
        assert str(confirm.label) == "Elegir"
        # botón Elegir arranca disabled hasta que haya un file highlighted
        assert confirm.disabled is True
