"""Cliente HTTP asíncrono para ElevenLabs API directa.

Reglas:
- HTTP puro: sin validación de dominio, sin lógica de negocio.
- Retries en 5xx y 429 con backoff exponencial.
- 4xx propaga como `ElevenLabsClientError` (no se reintenta), salvo
  créditos/tier insuficiente que se tipa aparte.
"""

from __future__ import annotations

import asyncio
from typing import Any, Final

import httpx
from loguru import logger

from ..domain.errors import (
    ElevenLabsClientError,
    ElevenLabsInsufficientCreditsError,
    ElevenLabsServerError,
)
from ..domain.policies import KIE_BACKOFF_BASE_SECONDS, KIE_MAX_RETRIES

_BASE_URL: Final[str] = "https://api.elevenlabs.io"
_MAX_RETRIES: Final[int] = KIE_MAX_RETRIES
_BACKOFF_BASE_SECONDS: Final[float] = KIE_BACKOFF_BASE_SECONDS
_CONNECT_TIMEOUT_SECONDS: Final[float] = 15.0
_TOTAL_TIMEOUT_SECONDS: Final[float] = 60.0
_HTTP_CLIENT_ERROR_START: Final[int] = 400
_HTTP_FORBIDDEN: Final[int] = 403
_HTTP_INSUFFICIENT_CREDITS: Final[int] = 402
_HTTP_RATE_LIMIT: Final[int] = 429
_HTTP_SERVER_ERROR_START: Final[int] = 500
_DEFAULT_PAGE_SIZE: Final[int] = 100
_DEFAULT_STS_MODEL: Final[str] = "eleven_multilingual_sts_v2"
_DEFAULT_OUTPUT_FORMAT: Final[str] = "mp3_44100_128"
_AUDIO_FILENAME: Final[str] = "audio.mp3"
_AUDIO_MIME_TYPE: Final[str] = "audio/mpeg"


class ElevenLabsClient:
    """Wrapper httpx para los endpoints directos de ElevenLabs usados por la app."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        if not api_key:
            logger.debug("ElevenLabsClient construido sin ELEVENLABS_API_KEY")
        self._client = httpx.AsyncClient(
            base_url=_BASE_URL,
            timeout=httpx.Timeout(_TOTAL_TIMEOUT_SECONDS, connect=_CONNECT_TIMEOUT_SECONDS),
            headers={"xi-api-key": api_key},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> ElevenLabsClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def list_voices(
        self,
        *,
        voice_type: str | None = None,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        """GET /v2/voices — lista voces disponibles con filtros opcionales."""
        params: dict[str, Any] = {"page_size": _DEFAULT_PAGE_SIZE}
        if voice_type is not None:
            params["voice_type"] = voice_type
        if search is not None:
            params["search"] = search
        payload = await self._request_json("GET", "/v2/voices", params=params)
        voices = payload.get("voices") if isinstance(payload, dict) else None
        if not isinstance(voices, list):
            raise ElevenLabsClientError(f"respuesta sin 'voices': {payload!r}")
        if not all(isinstance(voice, dict) for voice in voices):
            raise ElevenLabsClientError(f"respuesta inválida de /v2/voices: {payload!r}")
        return voices

    async def speech_to_speech(
        self,
        voice_id: str,
        audio: bytes,
        *,
        model_id: str = _DEFAULT_STS_MODEL,
        remove_background_noise: bool = False,
        output_format: str = _DEFAULT_OUTPUT_FORMAT,
    ) -> bytes:
        """POST /v1/speech-to-speech/{voice_id} — convierte audio en otra voz."""
        response = await self._request(
            "POST",
            f"/v1/speech-to-speech/{voice_id}",
            files={"audio": (_AUDIO_FILENAME, audio, _AUDIO_MIME_TYPE)},
            data={
                "model_id": model_id,
                "remove_background_noise": str(remove_background_noise).lower(),
            },
            params={"output_format": output_format},
        )
        return response.content

    async def list_models(self) -> list[dict[str, Any]]:
        """GET /v1/models — lista los modelos expuestos por ElevenLabs."""
        payload = await self._request_json("GET", "/v1/models")
        if not isinstance(payload, list):
            raise ElevenLabsClientError(f"respuesta inesperada de /v1/models: {payload!r}")
        if not all(isinstance(model, dict) for model in payload):
            raise ElevenLabsClientError(f"respuesta inválida de /v1/models: {payload!r}")
        return payload

    def _ensure_api_key(self) -> None:
        if not self._api_key:
            raise ElevenLabsClientError("No hay ELEVENLABS_API_KEY configurada.")

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Ejecuta la request con retries en 5xx/429 y traduce errores al dominio."""
        self._ensure_api_key()
        last_server_error: ElevenLabsServerError | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                response = await self._client.request(method, url, **kwargs)
            except httpx.TransportError as exc:
                last_server_error = ElevenLabsServerError(
                    f"error de red llamando a ElevenLabs ({method} {url}): {exc}"
                )
                if attempt < _MAX_RETRIES:
                    await self._sleep_before_retry(attempt, url, last_server_error)
                    continue
                break
            if response.is_success:
                return response
            if self._is_insufficient_credits_status(response.status_code):
                raise ElevenLabsInsufficientCreditsError(self._format_credit_error(response))
            if response.status_code == _HTTP_RATE_LIMIT:
                last_server_error = ElevenLabsServerError(self._format_http_error(response))
                if attempt < _MAX_RETRIES:
                    await self._sleep_before_retry(attempt, url, last_server_error)
                    continue
                break
            if _HTTP_CLIENT_ERROR_START <= response.status_code < _HTTP_SERVER_ERROR_START:
                raise ElevenLabsClientError(self._format_http_error(response))
            last_server_error = ElevenLabsServerError(self._format_http_error(response))
            if attempt < _MAX_RETRIES:
                await self._sleep_before_retry(attempt, url, last_server_error)
        assert last_server_error is not None  # noqa: S101 (invariante: error transitorio agotado)
        raise last_server_error

    async def _request_json(self, method: str, url: str, **kwargs: Any) -> dict[str, Any] | list[Any]:
        """Ejecuta la request y parsea JSON preservando la jerarquía de errores."""
        response = await self._request(method, url, **kwargs)
        try:
            parsed: dict[str, Any] | list[Any] = response.json()
        except ValueError as exc:
            raise ElevenLabsClientError(
                f"respuesta JSON inválida de ElevenLabs ({method} {url})"
            ) from exc
        return parsed

    async def _sleep_before_retry(
        self,
        attempt: int,
        url: str,
        error: ElevenLabsServerError,
    ) -> None:
        wait_seconds = _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
        logger.warning(
            "Retry ElevenLabs attempt={}/{} url={} wait={}s error={}",
            attempt,
            _MAX_RETRIES,
            url,
            wait_seconds,
            error,
        )
        await asyncio.sleep(wait_seconds)

    @staticmethod
    def _is_insufficient_credits_status(status_code: int) -> bool:
        return status_code in {_HTTP_INSUFFICIENT_CREDITS, _HTTP_FORBIDDEN}

    @staticmethod
    def _format_http_error(response: httpx.Response) -> str:
        return (
            f"{response.request.method} {response.request.url} -> "
            f"HTTP {response.status_code} {response.reason_phrase}"
        )

    @staticmethod
    def _format_credit_error(response: httpx.Response) -> str:
        """Mensaje accionable para 402/403 con detalle del body si existe."""
        try:
            body = response.json()
        except ValueError:
            body = None
        api_msg = ElevenLabsClient._extract_error_message(body)
        suffix = f": {api_msg}" if api_msg else ""
        return (
            "Créditos insuficientes en ElevenLabs o tu tier no soporta esta operación"
            f"{suffix}."
        )

    @staticmethod
    def _extract_error_message(body: Any) -> str | None:
        if isinstance(body, dict):
            detail = body.get("detail")
            if isinstance(detail, dict):
                message = detail.get("message")
                if isinstance(message, str) and message:
                    return message
            if isinstance(detail, str) and detail:
                return detail
            message = body.get("message")
            if isinstance(message, str) and message:
                return message
        return None
