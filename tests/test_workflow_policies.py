"""Tests de los validators del workflow domain."""

from __future__ import annotations

import pytest

from kie_avatar_studio.domain.errors import (
    WorkflowStepValidationError,
    WorkflowValidationError,
)
from kie_avatar_studio.domain.models import (
    ImageAssetKind,
    ModelCreation,
    ModelCreationMethod,
    StepType,
    WorkflowJob,
    WorkflowPreSettings,
    WorkflowProgressKey,
    WorkflowProgressStatus,
    WorkflowStep,
)
from kie_avatar_studio.domain.policies import (
    MAX_I2V_PROMPT_CHARS,
    MAX_PROMPT_CHARS,
    expected_progress_keys_for_step,
    slugify_workflow_name,
    validate_i2v_duration,
    validate_model_creation,
    validate_workflow,
    validate_workflow_step,
)


def _make_step(
    *,
    step: int = 1,
    type_: StepType = StepType.A_ROLL,
    text: str = "Hola, esto es una prueba.",
    change_background: bool = False,
    prompt: str = "Una mujer hablando a cámara, plano medio.",
    background_description: str = "",
) -> WorkflowStep:
    return WorkflowStep(
        step=step,
        scene_name=f"Escena {step}",
        scene_slug=f"escena_{step}",
        type=type_,
        change_background=change_background,
        background_description=background_description,
        prompt=prompt,
        text=text,
    )


def _make_workflow(steps: list[WorkflowStep]) -> WorkflowJob:
    return WorkflowJob(
        id="wf_test_001",
        name="Test Workflow",
        slug="test_workflow",
        source_json_path="workflows/test.json",
        output_dir="outputs/wf_test_001",
        pre_settings=WorkflowPreSettings(
            voice_preset_id="default",
            model_creation=ModelCreation(method=ModelCreationMethod.PROMPT, prompt="A woman"),
        ),
        steps=steps,
    )


# --- slugify -------------------------------------------------------------


class TestSlugifyWorkflowName:
    def test_basic_lowercase_with_underscore(self) -> None:
        assert slugify_workflow_name("Video Creation Automation") == "video_creation_automation"

    def test_strips_leading_trailing_punctuation(self) -> None:
        assert slugify_workflow_name("  !!Hola Mundo!!  ") == "hola_mundo"

    def test_empty_or_punctuation_only_returns_default(self) -> None:
        assert slugify_workflow_name("") == "workflow"
        assert slugify_workflow_name("   ") == "workflow"
        assert slugify_workflow_name("!@#$%") == "workflow"

    def test_collapses_consecutive_separators(self) -> None:
        assert slugify_workflow_name("foo - bar - baz") == "foo_bar_baz"

    def test_truncates_long_names(self) -> None:
        long = "a" * 200
        slug = slugify_workflow_name(long)
        assert len(slug) <= 120

    def test_unicode_chars_get_replaced(self) -> None:
        # Acentos no son [a-z0-9] así que se reemplazan; el slug sigue siendo válido.
        assert slugify_workflow_name("Niña Bonita") == "ni_a_bonita"


# --- model_creation -------------------------------------------------------


class TestValidateModelCreation:
    def test_prompt_requires_non_empty_prompt(self) -> None:
        with pytest.raises(WorkflowValidationError, match="requiere 'prompt'"):
            validate_model_creation(ModelCreation(method=ModelCreationMethod.PROMPT))

    def test_prompt_valid_with_text(self) -> None:
        validate_model_creation(
            ModelCreation(method=ModelCreationMethod.PROMPT, prompt="A photorealistic woman")
        )

    def test_local_requires_non_empty_path(self) -> None:
        with pytest.raises(WorkflowValidationError, match="requiere 'local_path'"):
            validate_model_creation(ModelCreation(method=ModelCreationMethod.LOCAL))

    def test_local_valid_with_path(self) -> None:
        validate_model_creation(
            ModelCreation(method=ModelCreationMethod.LOCAL, local_path="inputs/m.png")
        )

    def test_catalog_requires_kind_and_id(self) -> None:
        with pytest.raises(WorkflowValidationError, match="asset_kind"):
            validate_model_creation(ModelCreation(method=ModelCreationMethod.CATALOG))

    def test_catalog_valid_with_kind_and_id(self) -> None:
        validate_model_creation(
            ModelCreation(
                method=ModelCreationMethod.CATALOG,
                asset_kind=ImageAssetKind.UPLOADED,
                asset_id="img_x",
            )
        )

    def test_prompt_too_long_raises(self) -> None:
        with pytest.raises(WorkflowValidationError, match=r"model_creation\.prompt inválido"):
            validate_model_creation(
                ModelCreation(method=ModelCreationMethod.PROMPT, prompt="A" * 20_001)
            )


# --- workflow_step --------------------------------------------------------


class TestValidateWorkflowStep:
    def test_valid_a_roll_returns_empty_warnings(self) -> None:
        warnings = validate_workflow_step(_make_step())
        assert warnings == []

    def test_a_roll_without_text_raises(self) -> None:
        with pytest.raises(WorkflowStepValidationError, match="a-roll requiere 'text'"):
            validate_workflow_step(_make_step(text=""))

    def test_b_roll_without_text_is_valid(self) -> None:
        warnings = validate_workflow_step(_make_step(type_=StepType.B_ROLL, text="", change_background=True))
        # change_background=True + bg_desc="" emite warning de bg
        assert any("background_description vacío" in w for w in warnings)

    def test_b_roll_with_change_background_false_emits_warning(self) -> None:
        warnings = validate_workflow_step(
            _make_step(type_=StepType.B_ROLL, text="", change_background=False)
        )
        assert any("change_background=false" in w for w in warnings)

    def test_step_number_must_be_positive(self) -> None:
        with pytest.raises(WorkflowStepValidationError, match=">= 1"):
            validate_workflow_step(_make_step(step=0))

    def test_empty_prompt_raises(self) -> None:
        with pytest.raises(WorkflowStepValidationError, match="prompt vacío"):
            validate_workflow_step(_make_step(prompt=""))

    def test_a_roll_prompt_too_long_raises(self) -> None:
        with pytest.raises(WorkflowStepValidationError, match="supera"):
            validate_workflow_step(_make_step(prompt="x" * (MAX_PROMPT_CHARS + 1)))

    def test_b_roll_prompt_too_long_raises(self) -> None:
        # B-roll usa el límite del i2v (2500), no el del Avatar Pro (5000).
        with pytest.raises(WorkflowStepValidationError, match="supera"):
            validate_workflow_step(
                _make_step(
                    type_=StepType.B_ROLL,
                    text="",
                    change_background=True,
                    background_description="x",
                    prompt="y" * (MAX_I2V_PROMPT_CHARS + 1),
                )
            )

    def test_b_roll_with_text_validates_text_as_tts(self) -> None:
        # text con whitespace solo se rechaza como TTS
        with pytest.raises(WorkflowStepValidationError, match="text inválido"):
            validate_workflow_step(
                _make_step(type_=StepType.B_ROLL, text="  ", change_background=True, background_description="x")
            )

    def test_progress_with_invalid_key_for_type_raises(self) -> None:
        # A-roll NO usa DOWNLOAD_VIDEO/DOWNLOAD_AUDIO (esas son de b-roll con text).
        step = _make_step()
        step.progress[WorkflowProgressKey.DOWNLOAD_VIDEO] = WorkflowProgressStatus.PENDING
        with pytest.raises(WorkflowStepValidationError, match="progress tiene keys inválidas"):
            validate_workflow_step(step)


# --- expected_progress_keys helper ----------------------------------------


class TestExpectedProgressKeys:
    def test_a_roll_expects_4_keys(self) -> None:
        keys = expected_progress_keys_for_step(_make_step())
        assert keys == frozenset(
            {
                WorkflowProgressKey.SCENE_IMAGE,
                WorkflowProgressKey.AUDIO,
                WorkflowProgressKey.VIDEO,
                WorkflowProgressKey.DOWNLOAD,
            }
        )

    def test_b_roll_with_text_expects_5_keys(self) -> None:
        keys = expected_progress_keys_for_step(
            _make_step(type_=StepType.B_ROLL, text="Hola.", change_background=True)
        )
        assert WorkflowProgressKey.DOWNLOAD_VIDEO in keys
        assert WorkflowProgressKey.DOWNLOAD_AUDIO in keys
        assert WorkflowProgressKey.DOWNLOAD not in keys

    def test_b_roll_without_text_uses_simple_download(self) -> None:
        keys = expected_progress_keys_for_step(
            _make_step(type_=StepType.B_ROLL, text="", change_background=True)
        )
        assert keys == frozenset(
            {
                WorkflowProgressKey.SCENE_IMAGE,
                WorkflowProgressKey.VIDEO,
                WorkflowProgressKey.DOWNLOAD,
            }
        )
        assert WorkflowProgressKey.AUDIO not in keys


# --- validate_workflow ----------------------------------------------------


class TestValidateWorkflow:
    def test_valid_workflow_returns_empty_warnings(self) -> None:
        warnings = validate_workflow(_make_workflow([_make_step()]))
        assert warnings == []

    def test_empty_steps_raises(self) -> None:
        with pytest.raises(WorkflowValidationError, match="al menos 1 step"):
            validate_workflow(_make_workflow([]))

    def test_empty_name_raises(self) -> None:
        workflow = _make_workflow([_make_step()])
        workflow.name = "   "
        with pytest.raises(WorkflowValidationError, match="name"):
            validate_workflow(workflow)

    def test_non_consecutive_step_numbers_raises(self) -> None:
        steps = [_make_step(step=1), _make_step(step=3)]  # falta el 2
        with pytest.raises(WorkflowValidationError, match="consecutivos"):
            validate_workflow(_make_workflow(steps))

    def test_duplicate_step_numbers_raises(self) -> None:
        steps = [_make_step(step=1), _make_step(step=1)]
        with pytest.raises(WorkflowValidationError, match="consecutivos"):
            validate_workflow(_make_workflow(steps))

    def test_aggregates_warnings_from_steps(self) -> None:
        steps = [
            _make_step(),
            _make_step(step=2, type_=StepType.B_ROLL, text="", change_background=False),
        ]
        warnings = validate_workflow(_make_workflow(steps))
        # Step 2 emite warning de change_background=false
        assert any("change_background=false" in w for w in warnings)

    def test_invalid_model_creation_raises_at_workflow_level(self) -> None:
        workflow = _make_workflow([_make_step()])
        workflow.pre_settings.model_creation = ModelCreation(method=ModelCreationMethod.PROMPT)
        with pytest.raises(WorkflowValidationError, match="requiere 'prompt'"):
            validate_workflow(workflow)


# --- validate_i2v_duration ------------------------------------------------


class TestValidateI2vDuration:
    def test_5_is_valid(self) -> None:
        validate_i2v_duration(5)

    def test_10_is_valid(self) -> None:
        validate_i2v_duration(10)

    @pytest.mark.parametrize("invalid", [0, 1, 3, 7, 15, 30])
    def test_others_raise(self, invalid: int) -> None:
        with pytest.raises(WorkflowStepValidationError):
            validate_i2v_duration(invalid)
