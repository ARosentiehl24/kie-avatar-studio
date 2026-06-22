from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Final

import httpx
from loguru import logger

from ..domain.errors import (
    ElevenLabsClientError,
    ElevenLabsInsufficientCreditsError,
    ElevenLabsServerError,
)
from ..domain.models import (
    DEFAULT_VOICE_CHANGER_MODEL_ID,
    DEFAULT_VOICE_CHANGER_OUTPUT_FORMAT,
    VoiceSettings,
)
from ..domain.policies import (
    ELEVENLABS_CONNECT_TIMEOUT_SECONDS,
    ELEVENLABS_TOTAL_TIMEOUT_SECONDS,
    KIE_BACKOFF_BASE_SECONDS,
    KIE_MAX_RETRIES,
)
from ._streaming import write_response_to_file

_BASE_URL: Final[str] = "https://api.elevenlabs.io"
_MAX_RETRIES: Final[int] = KIE_MAX_RETRIES
_BACKOFF_BASE_SECONDS: Final[float] = KIE_BACKOFF_BASE_SECONDS
_HTTP_CLIENT_ERROR_START: Final[int] = 400
_HTTP_FORBIDDEN: Final[int] = 403
_HTTP_INSUFFICIENT_CREDITS: Final[int] = 402
_HTTP_SERVER_ERROR_START: Final[int] = 500
_DEFAULT_PAGE_SIZE: Final[int] = 100
_AUDIO_FILENAME: Final[str] = "audio.mp3"
_AUDIO_MIME_TYPE: Final[str] = "audio/mpeg"
JsonObject = dict[str, Any]  # Any: objeto JSON externo de ElevenLabs.
JsonArray = list[Any]  # Any: array JSON externo de ElevenLabs.
JsonPayload = JsonObject | JsonArray
ErrorPayload = Any  # Any: detalle de error externo no controlado por el dominio.
HttpxKwarg = Any  # Any: kwargs heterogéneos aceptados por httpx.request/stream.


class ElevenLabsClient:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        if not api_key:
            logger.debug("ElevenLabsClient construido sin ELEVENLABS_API_KEY")
        self._client = httpx.AsyncClient(
            base_url=_BASE_URL,
            timeout=httpx.Timeout(
                ELEVENLABS_TOTAL_TIMEOUT_SECONDS,
                connect=ELEVENLABS_CONNECT_TIMEOUT_SECONDS,
            ),
            headers={"xi-api-key": api_key},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def list_voices(
        self,
        *,
        voice_type: str | None = None,
        search: str | None = None,
    ) -> list[JsonObject]:
        params: JsonObject = {"page_size": _DEFAULT_PAGE_SIZE}
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

    async def speech_to_speech_to_file(
        self,
        voice_id: str,
        audio_path: Path,
        output_path: Path,
        *,
        model_id: str = DEFAULT_VOICE_CHANGER_MODEL_ID,
        remove_background_noise: bool = False,
        output_format: str = DEFAULT_VOICE_CHANGER_OUTPUT_FORMAT,
        voice_settings: VoiceSettings | None = None,
    ) -> Path:
        data = self._build_sts_data(
            model_id=model_id,
            remove_background_noise=remove_background_noise,
            voice_settings=voice_settings,
        )
        return await self._stream_request_to_file(
            "POST",
            f"/v1/speech-to-speech/{voice_id}",
            audio_path=audio_path,
            output_path=output_path,
            data=data,
            params={"output_format": output_format},
        )

    async def list_models(self) -> list[JsonObject]:
        payload = await self._request_json("GET", "/v1/models")
        if not isinstance(payload, list):
            raise ElevenLabsClientError(f"respuesta inesperada de /v1/models: {payload!r}")
        if not all(isinstance(model, dict) for model in payload):
            raise ElevenLabsClientError(f"respuesta inválida de /v1/models: {payload!r}")
        return payload

    def _ensure_api_key(self) -> None:
        if not self._api_key:
            raise ElevenLabsClientError("No hay ELEVENLABS_API_KEY configurada.")

    async def _request(self, method: str, url: str, **kwargs: HttpxKwarg) -> httpx.Response:
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
            if _HTTP_CLIENT_ERROR_START <= response.status_code < _HTTP_SERVER_ERROR_START:
                raise ElevenLabsClientError(self._format_http_error(response))
            last_server_error = ElevenLabsServerError(self._format_http_error(response))
            if attempt < _MAX_RETRIES:
                await self._sleep_before_retry(attempt, url, last_server_error)
        assert last_server_error is not None  # noqa: S101 (invariante: error transitorio agotado)
        raise last_server_error

    async def _request_json(self, method: str, url: str, **kwargs: HttpxKwarg) -> JsonPayload:
        response = await self._request(method, url, **kwargs)
        try:
            parsed: JsonPayload = response.json()
        except ValueError as exc:
            raise ElevenLabsClientError(
                f"respuesta JSON inválida de ElevenLabs ({method} {url})"
            ) from exc
        return parsed

    async def _stream_request_to_file(
        self,
        method: str,
        url: str,
        *,
        audio_path: Path,
        output_path: Path,
        **kwargs: HttpxKwarg,
    ) -> Path:
        self._ensure_api_key()
        last_server_error: ElevenLabsServerError | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                result = await self._stream_once_to_file(
                    method,
                    url,
                    audio_path=audio_path,
                    output_path=output_path,
                    **kwargs,
                )
                if isinstance(result, Path):
                    return result
                last_server_error = result
            except httpx.TransportError as exc:
                last_server_error = ElevenLabsServerError(
                    f"error de red llamando a ElevenLabs ({method} {url}): {exc}"
                )
            if attempt < _MAX_RETRIES and last_server_error is not None:
                await self._sleep_before_retry(attempt, url, last_server_error)
                continue
            break
        assert last_server_error is not None  # noqa: S101 (invariante: error transitorio agotado)
        raise last_server_error

    async def _stream_once_to_file(
        self,
        method: str,
        url: str,
        *,
        audio_path: Path,
        output_path: Path,
        **kwargs: HttpxKwarg,
    ) -> Path | ElevenLabsServerError:
        audio_file = await asyncio.to_thread(audio_path.open, "rb")
        try:
            async with self._client.stream(
                method,
                url,
                files={"audio": (_AUDIO_FILENAME, audio_file, _AUDIO_MIME_TYPE)},
                **kwargs,
            ) as response:
                if response.is_success:
                    return await write_response_to_file(response, output_path)
                return await self._stream_error(response)
        finally:
            await asyncio.to_thread(audio_file.close)

    async def _stream_error(self, response: httpx.Response) -> ElevenLabsServerError:
        if self._is_insufficient_credits_status(response.status_code):
            await response.aread()
            raise ElevenLabsInsufficientCreditsError(self._format_credit_error(response))
        if _HTTP_CLIENT_ERROR_START <= response.status_code < _HTTP_SERVER_ERROR_START:
            raise ElevenLabsClientError(self._format_http_error(response))
        return ElevenLabsServerError(self._format_http_error(response))

    @classmethod
    def _build_sts_data(
        cls,
        *,
        model_id: str,
        remove_background_noise: bool,
        voice_settings: VoiceSettings | None,
    ) -> dict[str, str]:
        data = {
            "model_id": model_id,
            "remove_background_noise": str(remove_background_noise).lower(),
        }
        encoded_voice_settings = cls._encode_sts_voice_settings(voice_settings)
        if encoded_voice_settings is not None:
            data["voice_settings"] = encoded_voice_settings
        return data

    @staticmethod
    def _encode_sts_voice_settings(settings: VoiceSettings | None) -> str | None:
        if settings is None or settings.is_empty():
            return None
        payload = settings.model_dump(exclude_none=True)
        payload.pop("language_code", None)
        if not payload:
            return None
        return json.dumps(payload, separators=(",", ":"))

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
        try:
            body = response.json()
        except ValueError:
            body = None
        api_msg = ElevenLabsClient._extract_error_message(body)
        suffix = f": {api_msg}" if api_msg else ""
        return f"Créditos insuficientes en ElevenLabs o tu tier no soporta esta operación{suffix}."

    @staticmethod
    def _extract_error_message(body: ErrorPayload) -> str | None:
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
