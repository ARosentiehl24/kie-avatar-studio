from __future__ import annotations

from kie_avatar_studio.app_layer.visual_prompt_guard import (
    VISUAL_TEXT_GUARD,
    append_visual_text_guard,
)
from kie_avatar_studio.app_layer.workflow_execution_context import build_scene_prompt
from kie_avatar_studio.domain.models import StepType, WorkflowStep
from kie_avatar_studio.domain.policies import MAX_I2V_PROMPT_CHARS, MAX_PROMPT_CHARS


def test_append_visual_text_guard_adds_policy_once() -> None:
    prompt = append_visual_text_guard("Natural kitchen scene")
    assert "Natural kitchen scene" in prompt
    assert VISUAL_TEXT_GUARD in prompt
    assert append_visual_text_guard(prompt) == prompt


def test_append_visual_text_guard_preserves_near_limit_prompt() -> None:
    prompt = "x" * MAX_PROMPT_CHARS
    assert append_visual_text_guard(prompt) == prompt


def test_append_visual_text_guard_respects_i2v_limit() -> None:
    prompt = "x" * MAX_I2V_PROMPT_CHARS
    assert append_visual_text_guard(prompt, max_chars=MAX_I2V_PROMPT_CHARS) == prompt


def test_build_scene_prompt_applies_visual_text_guard() -> None:
    step = WorkflowStep(
        step=1,
        scene_name="B roll",
        scene_slug="b_roll",
        type=StepType.B_ROLL,
        prompt="A product on a table",
        change_scene=True,
        scene_description="Kitchen counter",
    )
    assert VISUAL_TEXT_GUARD in build_scene_prompt(step)
