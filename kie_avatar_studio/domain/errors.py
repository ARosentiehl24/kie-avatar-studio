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
