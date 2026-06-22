"""Regresiones del flujo de producto promocional en Automatización."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from kie_avatar_studio.domain.errors import KieServerError
from kie_avatar_studio.ui.screens.automation import AutomationScreen


class _FailingProductController:
    async def upload_local_product(self, _path: Path) -> None:
        raise KieServerError("error de red llamando a Kie")


class _RetryNeedsReloadController:
    async def ensure_product_ready_for_retry(self, _workflow_id: str) -> bool:
        return False


async def test_product_upload_failure_reopens_picker_preserving_base_ref(tmp_path: Path) -> None:
    """Si falla upload de producto, no se debe perder la base ya aprobada."""
    screen = object.__new__(AutomationScreen)
    screen._controller = _FailingProductController()
    entry = object()
    pre_settings = object()
    base_ref = object()
    captured: dict[str, Any] = {}

    screen._set_status = lambda *_args, **_kwargs: None

    def fake_retry_product_selection(given_entry: object, **kwargs: Any) -> None:
        captured["entry"] = given_entry
        captured.update(kwargs)

    screen._retry_product_selection = fake_retry_product_selection

    await screen._upload_product_and_open_summary(
        entry,
        audio_language=None,
        pre_settings=pre_settings,
        base_ref=base_ref,
        product_path=tmp_path / "producto.png",
    )

    assert captured["entry"] is entry
    assert captured["pre_settings"] is pre_settings
    assert captured["base_ref"] is base_ref


async def test_handle_retry_opens_product_picker_when_reload_required() -> None:
    screen = object.__new__(AutomationScreen)
    workflow = SimpleNamespace(
        id="wf_123",
        name="WF con producto",
        status=SimpleNamespace(value="partially_failed"),
        pre_settings=SimpleNamespace(product_image=None),
    )
    captured: dict[str, Any] = {}
    screen._controller = _RetryNeedsReloadController()

    async def fake_selected() -> object:
        return workflow

    screen._selected_db_workflow = fake_selected
    screen._set_status = lambda *args, **kwargs: captured.setdefault("status", args[0])
    screen._open_retry_product_picker = lambda wf: captured.setdefault("workflow", wf)
    screen._refresh_db_table = lambda: None

    await screen._handle_retry()

    assert captured["workflow"] is workflow
    assert "necesita recargar producto" in captured["status"]
