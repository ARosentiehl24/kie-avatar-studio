"""Jerarquía de errores del dominio.

Las capas superiores capturan exclusivamente estas excepciones para distinguir
fallos esperados (validación, HTTP, timeout) de fallos no manejados.
"""

from __future__ import annotations


class KieError(Exception):
    """Raíz de todos los errores relacionados con Kie.ai."""


class KieClientError(KieError):
    """HTTP 4xx desde Kie.ai. No se reintenta."""


class KieInsufficientCreditsError(KieClientError):
    """HTTP 402 desde Kie.ai: el saldo de la cuenta es insuficiente.

    Tipado aparte porque la UX es completamente distinta de un 4xx
    genérico: el usuario no puede arreglar el problema retocando inputs;
    tiene que cargar saldo en https://kie.ai/billing. Detectado por
    HTTP status code 402 o por `code: 402` dentro del body JSON
    (Kie a veces devuelve 200 + code:402 en el payload).
    """


class KieServerError(KieError):
    """HTTP 5xx persistente tras los reintentos configurados."""


class KieTimeoutError(KieError):
    """El polling de un task superó `task_timeout_seconds`."""


class ElevenLabsError(Exception):
    """Raíz de errores de ElevenLabs API directa."""


class ElevenLabsClientError(ElevenLabsError):
    """HTTP 4xx desde ElevenLabs. No se reintenta."""


class ElevenLabsInsufficientCreditsError(ElevenLabsClientError):
    """HTTP 402/403 — créditos insuficientes o tier no soporta la operación."""


class ElevenLabsServerError(ElevenLabsError):
    """HTTP 5xx persistente tras reintentos."""


class FFmpegError(Exception):
    """El subprocess de FFmpeg falló o el binario no está disponible."""


class JobValidationError(ValueError):
    """Un `VideoJob` no cumple las restricciones del dominio (chars, tamaño, formato)."""


class KeyValidationError(JobValidationError):
    """Una `KieKey` no cumple las restricciones de formato (label vacío, key corta, etc.)."""


class KeyNotFoundError(KieError):
    """Se intentó operar sobre una `KieKey` por id pero no existe en el store."""


class ImageValidationError(JobValidationError):
    """Una imagen no cumple las restricciones (formato, tamaño, archivo inexistente)."""


class ImageExpiredError(ImageValidationError):
    """La imagen referenciada ya expiró en Kie y no se puede reutilizar.

    Política oficial: los archivos subidos por File Upload API viven
    24 horas desde el upload (`KIE_UPLOAD_RETENTION_HOURS`). Para
    imágenes generadas (`GeneratedImage`) el límite es 14 días — ver
    `GeneratedImageExpiredError`. El usuario debe cargar una nueva.
    """


class ImageNotFoundError(KieError):
    """Se intentó operar sobre una `UploadedImage` por id pero no existe en el store."""


class UrlValidationError(JobValidationError):
    """Una URL no cumple el formato esperado (vacía, sin esquema, esquema no permitido)."""


class AudioValidationError(JobValidationError):
    """El audio (script, voice o settings) no cumple las restricciones de Kie."""


class AudioExpiredError(AudioValidationError):
    """El audio generado ya expiró en Kie y no puede reutilizarse en otro job.

    Política oficial: los archivos generados viven 14 días desde la creación
    (`KIE_GENERATED_RETENTION_DAYS`). El usuario debe regenerar el audio.
    """


class AudioNotFoundError(KieError):
    """Se intentó operar sobre un `GeneratedAudio` por id pero no existe en el store."""


class VoiceSettingsValidationError(AudioValidationError):
    """Los `VoiceSettings` están fuera de los rangos aceptados por Kie.

    Ver `policies.validate_voice_settings` para los rangos exactos por campo.
    """


class VoicePresetValidationError(JobValidationError):
    """Un `VoicePreset` no cumple las restricciones (label vacío, voice_id inválido,
    rangos de voice_settings fuera, etc.)."""


class VoicePresetNotFoundError(KieError):
    """Se intentó operar sobre un `VoicePreset` por id pero no existe."""


class ImageGenerationValidationError(JobValidationError):
    """Un `ImageJob` (Nano Banana 2) no cumple las restricciones del dominio.

    Cubre prompt vacío/largo, settings fuera del catálogo (aspect_ratio,
    resolution, output_format) y refs inválidas (más de 14, URL malformada,
    duplicados). Ver `policies.validate_image_prompt`,
    `policies.validate_image_settings` y `policies.validate_image_refs`.
    """


class GeneratedImageNotFoundError(KieError):
    """Se intentó operar sobre un `GeneratedImage` por id pero no existe."""


class GeneratedImageExpiredError(ImageGenerationValidationError):
    """La imagen generada referenciada ya expiró en Kie y no se puede reutilizar.

    Política oficial: las imágenes generadas viven 14 días desde la
    creación (`KIE_GENERATED_RETENTION_DAYS`). El usuario debe
    regenerar la imagen o usar otra.
    """


class WorkflowValidationError(JobValidationError):
    """Un `WorkflowJob` (automatización) no cumple las restricciones del dominio.

    Cubre shape inválido del JSON (steps faltantes, números no consecutivos,
    `model_creation` inconsistente con `method`, `pre_settings` faltantes)
    y la validación cruzada (preset_id que no existe en `VoicePresetStore`
    en momento de encolar — chequeada por el controller, no por el dominio).
    Ver `policies.validate_workflow`.
    """


class WorkflowStepValidationError(WorkflowValidationError):
    """Un `WorkflowStep` individual no cumple las restricciones.

    Cubre prompts vacíos/largos, `text` faltante en a-roll, `progress`
    con keys inválidas para el tipo del step, etc. Ver
    `policies.validate_workflow_step`.
    """


class WorkflowStepError(KieError):
    """Un step de un workflow falló durante la ejecución.

    Mensaje en español y opcionalmente referencia al `step.scene_name` para
    que la UI pueda mostrar contexto. Usado por el `WorkflowStepRunner`
    para distinguir fallas estructurales del workflow vs fallas Kie reales
    en el step (que se propagan tipadas más abajo).
    """


class StepAwaitingApprovalSignal(Exception):  # noqa: N818 - es señal de control, no error
    """Señal de control de flujo: el step quedó esperando aprobación humana.

    NO es un error — el step generó correctamente la scene_image con Nano
    Banana pero el workflow corre en modo `SceneApprovalMode.MANUAL` y
    necesita revisión humana antes de continuar al render Kling 3.0 (b-roll).

    El `WorkflowStepRunner._prepare_scene_image` la levanta después de
    persistir el `step.bg_image_job_id` + `step.scene_image_path` y poner
    el step en `WorkflowStepStatus.AWAITING_APPROVAL`. El `WorkflowRunner`
    la captura en `_run_one`, marca el workflow en
    `WorkflowStatus.AWAITING_APPROVAL` y termina su tarea sin avanzar a
    steps siguientes ni marcar el workflow como FAILED. El semáforo de
    workflows queda libre.

    Hereda de `Exception` (no `KieError`) para que sea detectable como
    señal específica sin chocar con el except genérico que captura errores
    del runner.
    """


class WorkflowNotFoundError(KieError):
    """Se intentó operar sobre un `WorkflowJob` por id pero no existe en el store."""
