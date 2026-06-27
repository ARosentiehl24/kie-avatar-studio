"""Smoke tests de `ConfigureWorkflowScreen`.

Foco: con muchos campos (voice changer + duración + aprobación + producto) en un
terminal chico, los controles deben quedar dentro de un `VerticalScroll`
(`#configure-workflow-body`) y los botones de acción deben seguir presentes
y alcanzables (no recortados por overflow).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from textual.containers import VerticalScroll
from textual.widgets import Button, Static

from kie_avatar_studio.app import KieAvatarStudioApp
from kie_avatar_studio.config import Settings
from kie_avatar_studio.domain.models import SceneApprovalMode, VoiceChangerSettings, WorkflowEntry
from kie_avatar_studio.domain.ports import ElevenLabsVoicesClient
from kie_avatar_studio.ui.screens.configure_workflow import ConfigureWorkflowScreen


class _FakeElevenLabsClient:
    async def list_voices(
        self,
        *,
        voice_type: str | None = None,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        _ = voice_type, search
        return [{"voice_id": "voice_123", "name": "Ana"}]

    async def list_models(self) -> list[dict[str, Any]]:
        return [{"model_id": "eleven_multilingual_sts_v2", "can_do_voice_conversion": True}]


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

    async def fake_check() -> float | None:
        return None

    app._check_credits = fake_check  # type: ignore[method-assign]
    return app


def _dense_entry() -> WorkflowEntry:
    """Workflow con TODOS los campos que el modal puede mostrar (maximiza alto):
    b-rolls con change_scene + producto promocional."""
    payload = {
        "workflow": "Dense Config",
        "pre_settings": {
            "audio_language": "es-419",
            "scene_approval_mode": "manual",
            "promote_product": True,
            "model_creation": {"method": "catalog", "asset_kind": "generated", "asset_id": "x"},
        },
        "run": [
            {
                "step": 1,
                "scene_name": "Hook",
                "type": "a-roll",
                "change_scene": False,
                "prompt": "Mujer hablando a cámara",
                "text": "Hola",
                "include_product": True,
                "product_prompt": "Sostiene el frasco",
            },
            {
                "step": 2,
                "scene_name": "B Roll",
                "type": "b-roll",
                "change_scene": True,
                "scene_description": "Cocina",
                "prompt": "Plano del producto",
                "text": "Mirá esto",
                "duration_seconds": 5,
            },
        ],
    }
    return WorkflowEntry(name="dense", path=Path("workflows/dense.json"), workflow_payload=payload)


def _push_configure(
    app: KieAvatarStudioApp,
    *,
    elevenlabs_client: ElevenLabsVoicesClient | None = None,
    entry: WorkflowEntry | None = None,
) -> ConfigureWorkflowScreen:
    screen = ConfigureWorkflowScreen(
        entry=entry or _dense_entry(),
        default_i2v_duration_seconds=app.settings.default_i2v_duration_seconds,
        default_scene_approval_mode=SceneApprovalMode(app.settings.default_scene_approval_mode),
        elevenlabs_client=elevenlabs_client,
    )
    app.push_screen(screen)
    return screen


async def test_configure_form_fields_live_in_scrollable_body(tmp_path: Path) -> None:
    """Los controles del formulario están dentro del `VerticalScroll` del body."""
    app = _build_app(tmp_path)
    # Terminal deliberadamente bajo para forzar overflow del formulario.
    async with app.run_test(size=(120, 24)) as pilot:
        await pilot.pause()
        _push_configure(app)
        await pilot.pause()
        body = app.screen.query_one("#configure-workflow-body", VerticalScroll)
        # El selector de voice changer y el de aprobación viven DENTRO del body scrollable.
        select_ids = {s.id for s in body.query("Select")}
        assert "configure-duration-select" in select_ids
        assert "configure-approval-select" in select_ids


async def test_configure_action_buttons_present_under_overflow(tmp_path: Path) -> None:
    """Aun con overflow, los botones de acción (Continuar/Cancelar) existen y
    NO están dentro del body scrollable (quedan fijos abajo, alcanzables)."""
    app = _build_app(tmp_path)
    async with app.run_test(size=(120, 24)) as pilot:
        await pilot.pause()
        _push_configure(app)
        await pilot.pause()
        confirm = app.screen.query_one("#configure-confirm", Button)
        cancel = app.screen.query_one("#configure-cancel", Button)
        assert confirm is not None
        assert cancel is not None
        # Los botones NO deben estar dentro del body scrollable.
        body = app.screen.query_one("#configure-workflow-body", VerticalScroll)
        body_button_ids = {b.id for b in body.query("Button")}
        assert "configure-confirm" not in body_button_ids
        assert "configure-cancel" not in body_button_ids


async def test_configure_approval_row_does_not_expand(tmp_path: Path) -> None:
    """Regresión del hueco: la fila del Select de aprobación debe tener altura
    fija (3), no expandirse a `height: 1fr` dejando un gap antes del hint."""
    app = _build_app(tmp_path)
    async with app.run_test(size=(120, 70)) as pilot:
        await pilot.pause()
        _push_configure(app)
        await pilot.pause()
        approval_row = app.screen.query_one("#configure-approval-row")
        duration_row = app.screen.query_one("#configure-duration-row")
        # Ambas filas de control deben medir lo mismo (3); si la de aprobación
        # se expandiera, su outer_size sería mucho mayor → el bug del hueco.
        assert approval_row.outer_size.height == 3
        assert duration_row.outer_size.height == 3


async def test_configure_disables_voice_changer_without_api_key(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.pause()
        _push_configure(app)
        await pilot.pause()
        select_button = app.screen.query_one("#configure-voice-changer-select", Button)
        hint = app.screen.query_one("#configure-voice-changer-hint", Static)
        assert select_button.disabled
        assert "Configura ELEVENLABS_API_KEY en .env para usar el voice changer" in str(
            hint.content
        )


async def test_configure_shows_voice_catalog_button_when_api_key_exists(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.pause()
        _push_configure(app, elevenlabs_client=_FakeElevenLabsClient())
        await pilot.pause()
        body = app.screen.query_one("#configure-workflow-body", VerticalScroll)
        select_button = body.query_one("#configure-voice-changer-select", Button)
        assert not select_button.disabled
        assert "Elegir voz" in str(select_button.label)
        assert select_button.outer_size.width > 0
        assert select_button.outer_size.height == 3


async def test_configure_shows_existing_voice_changer_selection(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    entry = _dense_entry()
    assert entry.workflow_payload is not None
    assert isinstance(entry.workflow_payload["pre_settings"], dict)
    entry.workflow_payload["pre_settings"]["voice_changer"] = VoiceChangerSettings(
        voice_id="voice_123",
        model_id="eleven_custom",
    ).model_dump(mode="json")
    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.pause()
        _push_configure(app, entry=entry)
        await pilot.pause()
        summary = app.screen.query_one("#configure-voice-changer-value", Static)
        rendered = str(summary.content)
        assert "voice_123" in rendered
        assert "eleven_custom" in rendered
