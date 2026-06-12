"""Regresiones del flujo de producto promocional en Automatización."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kie_avatar_studio.domain.errors import KieServerError
from kie_avatar_studio.ui.screens.automation import AutomationScreen


class _FailingProductController:
    async def upload_local_product(self, _path: Path) -> None:
        raise KieServerError("error de red llamando a Kie")


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
        voice_preset_id="narradora_adulta_ugc",
        audio_language=None,
        pre_settings=pre_settings,
        base_ref=base_ref,
        product_path=tmp_path / "producto.png",
    )

    assert captured["entry"] is entry
    assert captured["pre_settings"] is pre_settings
    assert captured["base_ref"] is base_ref
    assert captured["voice_preset_id"] == "narradora_adulta_ugc"
