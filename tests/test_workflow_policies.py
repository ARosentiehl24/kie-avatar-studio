"""Tests de los validators del workflow domain."""

from __future__ import annotations

import pytest

from kie_avatar_studio.app_layer.workflow_execution_context import WorkflowExecutionContext
from kie_avatar_studio.domain.errors import (
    WorkflowStepValidationError,
    WorkflowValidationError,
)
from kie_avatar_studio.domain.models import (
    ImageAssetKind,
    ImageAssetRef,
    ModelCreation,
    ModelCreationMethod,
    StepType,
    VoiceChangerSettings,
    VoiceSettings,
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
    parse_optional_int_field,
    resolve_effective_i2v_duration,
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
    change_scene: bool = False,
    prompt: str = "Una mujer hablando a cámara, plano medio.",
    scene_description: str = "",
    duration_seconds: int | None = None,
    include_model: bool = True,
    include_product: bool = False,
) -> WorkflowStep:
    return WorkflowStep(
        step=step,
        scene_name=f"Escena {step}",
        scene_slug=f"escena_{step}",
        type=type_,
        change_scene=change_scene,
        scene_description=scene_description,
        prompt=prompt,
        text=text,
        duration_seconds=duration_seconds,
        include_model=include_model,
        include_product=include_product,
    )


def _make_workflow(steps: list[WorkflowStep]) -> WorkflowJob:
    return WorkflowJob(
        id="wf_test_001",
        name="Test Workflow",
        slug="test_workflow",
        source_json_path="workflows/test.json",
        output_dir="outputs/wf_test_001",
        pre_settings=WorkflowPreSettings(
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

    def test_local_without_path_is_now_valid_lazy(self) -> None:
        """method=local sin path es válido: la UI completa el path en runtime."""
        # No debe lanzar: el path lo elige el usuario después via picker.
        validate_model_creation(ModelCreation(method=ModelCreationMethod.LOCAL))
        validate_model_creation(ModelCreation(method=ModelCreationMethod.LOCAL, local_path=""))

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
        warnings = validate_workflow_step(
            _make_step(type_=StepType.B_ROLL, text="", change_scene=True)
        )
        # change_scene=True + bg_desc="" emite warning de bg
        assert any("scene_description vacío" in w for w in warnings)

    def test_b_roll_with_change_scene_false_emits_warning(self) -> None:
        warnings = validate_workflow_step(
            _make_step(type_=StepType.B_ROLL, text="", change_scene=False)
        )
        assert any("change_scene=false" in w for w in warnings)

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
                    change_scene=True,
                    scene_description="x",
                    prompt="y" * (MAX_I2V_PROMPT_CHARS + 1),
                )
            )

    def test_b_roll_with_text_warns_that_text_is_ignored(self) -> None:
        warnings = validate_workflow_step(
            _make_step(
                type_=StepType.B_ROLL,
                text="Voz en off que debe ignorarse.",
                change_scene=True,
                scene_description="x",
            )
        )
        assert any("b-roll ignora el campo 'text'" in warning for warning in warnings)

    def test_c_roll_rules_warn_when_shape_is_not_clean_unreal(self) -> None:
        warnings = validate_workflow_step(
            _make_step(
                type_=StepType.C_ROLL,
                text="Voz que no debe ir.",
                change_scene=False,
                include_model=True,
                include_product=True,
            )
        )
        assert any("c-roll debería usar change_scene=true" in warning for warning in warnings)
        assert any("c-roll requiere scene_description" in warning for warning in warnings)
        assert any("c-roll ignora el campo 'text'" in warning for warning in warnings)
        assert any("include_model=false" in warning for warning in warnings)
        assert any("no debería ser toma de producto" in warning for warning in warnings)

    def test_progress_with_invalid_key_for_type_raises(self) -> None:
        # A-roll NO usa DOWNLOAD_VIDEO/DOWNLOAD_AUDIO (esas son de b-roll con text).
        step = _make_step()
        step.progress[WorkflowProgressKey.DOWNLOAD_VIDEO] = WorkflowProgressStatus.PENDING
        with pytest.raises(WorkflowStepValidationError, match="progress inválido para tipo"):
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

    def test_b_roll_with_text_still_uses_simple_download(self) -> None:
        keys = expected_progress_keys_for_step(
            _make_step(type_=StepType.B_ROLL, text="Hola.", change_scene=True)
        )
        assert keys == frozenset(
            {
                WorkflowProgressKey.SCENE_IMAGE,
                WorkflowProgressKey.VIDEO,
                WorkflowProgressKey.DOWNLOAD,
            }
        )
        assert WorkflowProgressKey.AUDIO not in keys

    def test_b_roll_without_text_uses_simple_download(self) -> None:
        keys = expected_progress_keys_for_step(
            _make_step(type_=StepType.B_ROLL, text="", change_scene=True)
        )
        assert keys == frozenset(
            {
                WorkflowProgressKey.SCENE_IMAGE,
                WorkflowProgressKey.VIDEO,
                WorkflowProgressKey.DOWNLOAD,
            }
        )
        assert WorkflowProgressKey.AUDIO not in keys

    def test_c_roll_uses_simple_download(self) -> None:
        keys = expected_progress_keys_for_step(
            _make_step(type_=StepType.C_ROLL, text="", change_scene=True)
        )
        assert keys == frozenset(
            {
                WorkflowProgressKey.SCENE_IMAGE,
                WorkflowProgressKey.VIDEO,
                WorkflowProgressKey.DOWNLOAD,
            }
        )


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
            _make_step(step=2, type_=StepType.B_ROLL, text="", change_scene=False),
        ]
        warnings = validate_workflow(_make_workflow(steps))
        # Step 2 emite warning de change_scene=false
        assert any("change_scene=false" in w for w in warnings)

    def test_invalid_model_creation_raises_at_workflow_level(self) -> None:
        workflow = _make_workflow([_make_step()])
        workflow.pre_settings.model_creation = ModelCreation(method=ModelCreationMethod.PROMPT)
        with pytest.raises(WorkflowValidationError, match="requiere 'prompt'"):
            validate_workflow(workflow)

    def test_invalid_veo_settings_raise_at_workflow_level(self) -> None:
        workflow = _make_workflow([_make_step()])
        workflow.pre_settings.veo.aspect_ratio = "4:3"
        with pytest.raises(WorkflowValidationError, match="aspect_ratio VEO inválido"):
            validate_workflow(workflow)

    def test_voice_changer_rejects_language_code_for_sts(self) -> None:
        workflow = _make_workflow([_make_step()])
        workflow.pre_settings.voice_changer = VoiceChangerSettings(
            voice_id="voice_123",
            voice_settings=VoiceSettings(language_code="es"),
        )
        with pytest.raises(WorkflowValidationError, match="language_code no aplica"):
            validate_workflow(workflow)


# --- validate_i2v_duration ------------------------------------------------


class TestValidateI2vDuration:
    @pytest.mark.parametrize("valid", [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15])
    def test_in_range_is_valid(self, valid: int) -> None:
        validate_i2v_duration(valid)

    @pytest.mark.parametrize("invalid", [0, 1, 2, 16, 20, 30])
    def test_out_of_range_raises(self, invalid: int) -> None:
        with pytest.raises(WorkflowStepValidationError):
            validate_i2v_duration(invalid)


# --- WorkflowStep.duration_seconds (b-roll i2v) ---------------------------


class TestStepDurationValidation:
    def test_b_roll_with_valid_duration_5_passes(self) -> None:
        warnings = validate_workflow_step(
            _make_step(type_=StepType.B_ROLL, text="", change_scene=True, duration_seconds=5)
        )
        assert all("duration" not in w for w in warnings)

    def test_b_roll_with_valid_duration_10_passes(self) -> None:
        warnings = validate_workflow_step(
            _make_step(type_=StepType.B_ROLL, text="", change_scene=True, duration_seconds=10)
        )
        assert all("duration" not in w for w in warnings)

    def test_b_roll_with_invalid_duration_raises(self) -> None:
        # 16 está fuera del rango 3-15 que soporta Kling 3.0
        with pytest.raises(WorkflowStepValidationError, match="duration i2v inválido"):
            validate_workflow_step(
                _make_step(
                    type_=StepType.B_ROLL,
                    text="",
                    change_scene=True,
                    duration_seconds=16,
                )
            )

    def test_b_roll_without_duration_uses_fallback_no_warning(self) -> None:
        warnings = validate_workflow_step(
            _make_step(
                type_=StepType.B_ROLL,
                text="",
                change_scene=True,
                duration_seconds=None,
            )
        )
        assert all("duration" not in w for w in warnings)

    def test_a_roll_with_duration_emits_warning(self) -> None:
        warnings = validate_workflow_step(_make_step(type_=StepType.A_ROLL, duration_seconds=10))
        assert any("duration_seconds" in w and "a-roll" in w for w in warnings)

    def test_a_roll_with_duration_does_not_validate_against_i2v_durations(self) -> None:
        # Aunque 7 NO es válido para i2v, no debería levantar error en
        # a-roll: el validator de duración solo aplica a b-roll.
        warnings = validate_workflow_step(_make_step(type_=StepType.A_ROLL, duration_seconds=7))
        assert any("duration_seconds" in w for w in warnings)


class TestWorkflowPreSettingsDurationValidation:
    def test_valid_override_5_passes(self) -> None:
        workflow = _make_workflow([_make_step(type_=StepType.B_ROLL, text="")])
        workflow.pre_settings.i2v_duration_seconds = 5
        validate_workflow(workflow)  # no raises

    def test_valid_override_10_passes(self) -> None:
        workflow = _make_workflow([_make_step(type_=StepType.B_ROLL, text="")])
        workflow.pre_settings.i2v_duration_seconds = 10
        validate_workflow(workflow)  # no raises

    def test_invalid_override_raises(self) -> None:
        workflow = _make_workflow([_make_step(type_=StepType.B_ROLL, text="")])
        # 20 está fuera del rango 3-15
        workflow.pre_settings.i2v_duration_seconds = 20
        with pytest.raises(WorkflowStepValidationError, match="duration i2v inválido"):
            validate_workflow(workflow)


# --- WorkflowExecutionContext.resolve_i2v_duration (3-niveles) ------------


class TestResolveI2vDurationFallback:
    """Valida la precedencia: override > step > default."""

    def _make_ctx(self, override: int | None) -> WorkflowExecutionContext:
        from datetime import UTC, datetime, timedelta
        from pathlib import Path

        return WorkflowExecutionContext(
            audio_language=None,
            voice_id="V",
            voice_settings=None,
            base_image_ref=ImageAssetRef(
                kind=ImageAssetKind.GENERATED,
                id="i",
                label="test",
                kie_url="https://x/y",
                expires_at=datetime.now(UTC) + timedelta(days=1),
            ),
            output_dir=Path("/tmp/wf"),
            i2v_duration_seconds_override=override,
        )

    def test_override_wins_over_step_and_default(self) -> None:
        ctx = self._make_ctx(override=10)
        step = _make_step(type_=StepType.B_ROLL, text="", duration_seconds=5)
        assert ctx.resolve_i2v_duration(step, default=5) == 10

    def test_step_wins_over_default_when_no_override(self) -> None:
        ctx = self._make_ctx(override=None)
        step = _make_step(type_=StepType.B_ROLL, text="", duration_seconds=10)
        assert ctx.resolve_i2v_duration(step, default=5) == 10

    def test_default_used_when_no_override_no_step(self) -> None:
        ctx = self._make_ctx(override=None)
        step = _make_step(type_=StepType.B_ROLL, text="", duration_seconds=None)
        assert ctx.resolve_i2v_duration(step, default=5) == 5

    def test_override_5_still_wins_even_if_step_has_10(self) -> None:
        # Caso edge: el usuario fuerza 5 desde el modal pese a que el JSON
        # diga 10. El modal es la última palabra.
        ctx = self._make_ctx(override=5)
        step = _make_step(type_=StepType.B_ROLL, text="", duration_seconds=10)
        assert ctx.resolve_i2v_duration(step, default=10) == 5


# --- resolve_effective_i2v_duration (pure helper) -------------------------


class TestResolveEffectiveI2vDuration:
    """Tests directos del helper de dominio (sin armar el context completo)."""

    def test_override_takes_precedence(self) -> None:
        assert resolve_effective_i2v_duration(10, 5, 7) == 10

    def test_step_used_when_no_override(self) -> None:
        assert resolve_effective_i2v_duration(None, 10, 5) == 10

    def test_default_used_when_no_override_no_step(self) -> None:
        assert resolve_effective_i2v_duration(None, None, 5) == 5

    def test_override_none_falls_through_to_step(self) -> None:
        # `0` no es válido en I2V_DURATIONS pero el helper no valida —
        # solo aplica precedencia. La validación es responsabilidad de
        # `validate_i2v_duration`.
        assert resolve_effective_i2v_duration(None, 0, 5) == 0


# --- parse_optional_int_field (canónico, antes en workflow_loader) --------


class TestParseOptionalIntField:
    """El parser canónico que loader + UI summary comparten."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (5, 5),
            (10, 10),
            (0, 0),  # válido como int aunque dominio lo rechace
            ("5", 5),
            ("10", 10),
            (5.0, 5),
            (5.9, 5),  # truncación esperada de float
            (None, None),
            ("", None),
            ("   ", None),
            ("abc", None),
            ("10.5", None),  # str con float NO se acepta
            ([], None),
            ({}, None),
            (True, None),
            (False, None),
        ],
    )
    def test_parse_table(self, raw: object, expected: int | None) -> None:
        assert parse_optional_int_field(raw) == expected


# --- producto promocional (Round 6) --------------------------------------


class TestProductPromotion:
    """Validación cruzada de promote_product + include_product."""

    @staticmethod
    def _product_step(
        *, step: int = 1, include_product: bool, product_prompt: str = "x"
    ) -> WorkflowStep:
        return WorkflowStep(
            step=step,
            scene_name=f"Escena {step}",
            scene_slug=f"escena_{step}",
            type=StepType.A_ROLL,
            change_scene=False,
            prompt="Mujer hablando a cámara",
            text="Hola, te muestro algo.",
            include_product=include_product,
            product_prompt=product_prompt,
        )

    def test_include_product_without_promote_raises(self) -> None:
        workflow = _make_workflow([self._product_step(include_product=True)])
        workflow.pre_settings.promote_product = False
        with pytest.raises(WorkflowValidationError, match="include_product=true"):
            validate_workflow(workflow)

    def test_include_product_with_promote_is_valid(self) -> None:
        workflow = _make_workflow([self._product_step(include_product=True)])
        workflow.pre_settings.promote_product = True
        # No levanta; devuelve warnings (lista, posiblemente vacía).
        warnings = validate_workflow(workflow)
        assert isinstance(warnings, list)

    def test_promote_without_any_include_product_warns(self) -> None:
        workflow = _make_workflow([self._product_step(include_product=False)])
        workflow.pre_settings.promote_product = True
        warnings = validate_workflow(workflow)
        assert any("ningún step" in w for w in warnings)

    def test_include_product_empty_prompt_warns(self) -> None:
        step = self._product_step(include_product=True, product_prompt="")
        warnings = validate_workflow_step(step)
        assert any("product_prompt vacío" in w for w in warnings)

    def test_set_as_base_without_scene_generation_warns(self) -> None:
        step = self._product_step(include_product=False)
        step.set_as_base = True
        warnings = validate_workflow_step(step)
        assert any("set_as_base=true" in w for w in warnings)


class TestImageAspectRatio:
    """Validación del aspect ratio global de imágenes."""

    def test_valid_image_aspect_ratio_passes(self) -> None:
        workflow = _make_workflow([_make_step()])
        workflow.pre_settings.image_aspect_ratio = "9:16"
        validate_workflow(workflow)  # no debe levantar error

    def test_invalid_image_aspect_ratio_raises(self) -> None:
        workflow = _make_workflow([_make_step()])
        # "16:10" no está en ASPECT_RATIOS
        workflow.pre_settings.image_aspect_ratio = "16:10"
        with pytest.raises(WorkflowValidationError, match="image_aspect_ratio inválido"):
            validate_workflow(workflow)

    def test_valid_step_image_aspect_ratio_passes(self) -> None:
        step = _make_step()
        step.image_aspect_ratio = "9:16"
        validate_workflow_step(step)  # no debe levantar error

    def test_invalid_step_image_aspect_ratio_raises(self) -> None:
        step = _make_step()
        # "21:99" no está en ASPECT_RATIOS
        step.image_aspect_ratio = "21:99"
        with pytest.raises(WorkflowStepValidationError, match="image_aspect_ratio inválido"):
            validate_workflow_step(step)
