"""Cliente HTTP asíncrono para Kie.ai. Implementa `domain.ports.KieGateway`.

Reglas (SPEC §8):
- HTTP puro: sin validación de dominio, sin lógica de negocio.
- Retries solo en 5xx con backoff exponencial.
- 4xx propaga como `KieClientError` (no se reintenta).
- Descargas por streaming.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Final

import httpx
from loguru import logger

from ..config import Settings
from ..domain.errors import KieClientError, KieInsufficientCreditsError, KieServerError
from ..domain.models import KieTaskCreated, KieUploadResult, VoiceSettings

DEFAULT_UPLOAD_PATH: Final[str] = "images/avatar-models"
DEFAULT_TTS_MODEL: Final[str] = "elevenlabs/text-to-speech-multilingual-v2"
DEFAULT_AVATAR_MODEL: Final[str] = "kling/ai-avatar-pro"
DEFAULT_NANO_BANANA_MODEL: Final[str] = "nano-banana-2"

_DOWNLOAD_CHUNK_BYTES: Final[int] = 64 * 1024
_MAX_RETRIES: Final[int] = 3
_BACKOFF_BASE_SECONDS: Final[float] = 1.0
_CONNECT_TIMEOUT: Final[float] = 15.0
_TOTAL_TIMEOUT: Final[float] = 60.0
_HTTP_CLIENT_ERROR_START: Final[int] = 400
_HTTP_INSUFFICIENT_CREDITS: Final[int] = 402
_HTTP_SERVER_ERROR_START: Final[int] = 500


class KieClient:
    """Wrapper httpx para los endpoints de Kie.ai usados por la app."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        if not settings.kie_api_key:
            # No es warning: en runtime el composition root puede aplicar
            # una key activa de `keys.json` después de construir el cliente.
            # El warning final (si sigue vacía) lo emite `app.on_mount`.
            logger.debug("KieClient construido sin KIE_API_KEY; esperando key activa")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(_TOTAL_TIMEOUT, connect=_CONNECT_TIMEOUT),
            headers={"Authorization": f"Bearer {settings.kie_api_key}"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> KieClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    # --- API pública -------------------------------------------------------

    async def upload_file(
        self,
        file_path: str | Path,
        upload_path: str = DEFAULT_UPLOAD_PATH,
        file_name: str | None = None,
    ) -> KieUploadResult:
        """POST /api/file-stream-upload — sube imagen local y devuelve downloadUrl.

        Lee el archivo con `asyncio.to_thread` para no bloquear la event loop
        (CR-5.1). El payload se mantiene bajo el límite duro de imagen (10 MB,
        ver `domain/policies`).
        """
        url = f"{self._settings.kie_upload_base}/api/file-stream-upload"
        path = Path(file_path)
        name = file_name or path.name
        contents = await asyncio.to_thread(path.read_bytes)
        payload = await self._request_json(
            "POST",
            url,
            files={"file": (name, contents, "application/octet-stream")},
            data={"uploadPath": upload_path, "fileName": name},
        )
        data = self._extract_data(payload)
        return KieUploadResult(
            file_name=data["fileName"],
            file_path=data["filePath"],
            download_url=data["downloadUrl"],
            file_size=data.get("fileSize", 0),
            mime_type=data.get("mimeType", "application/octet-stream"),
        )

    async def create_tts_task(
        self,
        text: str,
        voice: str,
        *,
        model: str | None = None,
        voice_settings: VoiceSettings | None = None,
    ) -> KieTaskCreated:
        """POST /api/v1/jobs/createTask — crea task de TTS ElevenLabs.

        Si `model` es `None`, usa `DEFAULT_TTS_MODEL`
        (`elevenlabs/text-to-speech-multilingual-v2`).

        Si `voice_settings` es `None`, no se envía ningún ajuste extra: Kie
        aplica los defaults documentados (stability=0.5, similarity_boost=0.75,
        style=0, speed=1). Si se pasa, los campos no-None se mergean **planos
        dentro de `input`** (no anidados bajo `voice_settings`): ese es el
        shape real que documenta el OpenAPI spec del endpoint TTS de Kie.
        """
        chosen_model = model or DEFAULT_TTS_MODEL
        body_input: dict[str, Any] = {"text": text, "voice": voice}
        if voice_settings is not None:
            body_input.update(voice_settings.model_dump(exclude_none=True))
        body = {"model": chosen_model, "input": body_input}
        return await self._create_task(body)

    async def create_avatar_task(
        self,
        image_url: str,
        audio_url: str,
        prompt: str,
        model: str = DEFAULT_AVATAR_MODEL,
    ) -> KieTaskCreated:
        """POST /api/v1/jobs/createTask — crea task de Kling AI Avatar Pro."""
        body = {
            "model": model,
            "input": {"image_url": image_url, "audio_url": audio_url, "prompt": prompt},
        }
        return await self._create_task(body)

    async def create_nano_banana_task(
        self,
        prompt: str,
        *,
        image_input: list[str] | None = None,
        aspect_ratio: str = "auto",
        resolution: str = "1K",
        output_format: str = "jpg",
        model: str = DEFAULT_NANO_BANANA_MODEL,
    ) -> KieTaskCreated:
        """POST /api/v1/jobs/createTask — crea task de Nano Banana 2 (Google).

        El input acepta hasta 14 URLs de referencia en `image_input` (deben ser
        URLs públicas; típicamente las que devuelve `upload_file` o `kie_url` de
        un `GeneratedImage`). Los valores aceptados por `aspect_ratio`,
        `resolution` y `output_format` están listados en `domain.policies`
        (`ASPECT_RATIOS`, `RESOLUTIONS`, `OUTPUT_FORMATS`); el cliente NO los
        valida — lo hace `policies.validate_image_settings` antes de llamar.
        Mantenerlo así respeta CR-2.1 (KieClient = solo HTTP).

        Si `image_input` es `None`, enviamos lista vacía: es el valor que el
        OpenAPI spec espera para text-to-image puro.
        """
        body = {
            "model": model,
            "input": {
                "prompt": prompt,
                "image_input": image_input or [],
                "aspect_ratio": aspect_ratio,
                "resolution": resolution,
                "output_format": output_format,
            },
        }
        return await self._create_task(body)

    async def get_account_credits(self) -> float:
        """GET /api/v1/chat/credit — devuelve el saldo actual en créditos.

        Endpoint barato (sin costo en créditos) que sirve también como smoke
        test de la API key: si responde 200 con un número, la key es válida y
        tenemos conectividad. Si responde 401, la key está mal.

        Lanza `KieClientError` si la respuesta no tiene la forma esperada
        (la única vez que el dominio sale a infra para validar shape).
        """
        url = f"{self._settings.kie_api_base}/api/v1/chat/credit"
        payload = await self._request_json("GET", url)
        balance = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(balance, int | float):
            raise KieClientError(f"respuesta inesperada de /chat/credit: {payload!r}")
        return float(balance)

    async def get_task_detail(self, task_id: str) -> dict[str, Any]:
        """GET /api/v1/jobs/recordInfo?taskId=...

        El shape exacto está pendiente de confirmar; ver `domain.policies` para la
        normalización de status y la extracción de URLs.
        """
        url = f"{self._settings.kie_api_base}/api/v1/jobs/recordInfo"
        return await self._request_json("GET", url, params={"taskId": task_id})

    async def download_file(self, url: str, output_path: str | Path) -> Path:
        """Descarga archivo binario (audio/video) por streaming, sin cargar en memoria.

        Abre y escribe el archivo en un thread auxiliar (`asyncio.to_thread`) para no
        bloquear la event loop con IO de disco síncrona (CR-5.1).
        """
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        file_handle = await asyncio.to_thread(out.open, "wb")
        try:
            async with self._client.stream("GET", url) as response:
                self._raise_for_status(response)
                async for chunk in response.aiter_bytes(chunk_size=_DOWNLOAD_CHUNK_BYTES):
                    await asyncio.to_thread(file_handle.write, chunk)
        finally:
            await asyncio.to_thread(file_handle.close)
        return out

    # --- helpers internos --------------------------------------------------

    async def _create_task(self, body: dict[str, Any]) -> KieTaskCreated:
        url = f"{self._settings.kie_api_base}/api/v1/jobs/createTask"
        payload = await self._request_json("POST", url, json=body)
        data = self._extract_data(payload)
        return KieTaskCreated(task_id=data["taskId"])

    async def _request_json(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        """Ejecuta la request con retries en 5xx y traduce errores a `KieError`.

        Detecta dos formas en que Kie reporta créditos insuficientes:
        1. HTTP 402 (camino estándar — observado en `createTask`).
        2. HTTP 200 con `code: 402` dentro del body JSON (algunas variantes).
        Ambas se mapean a `KieInsufficientCreditsError` para que el caller
        las distinga del resto de 4xx.
        """
        last_server_error: KieServerError | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            response = await self._client.request(method, url, **kwargs)
            if response.is_success:
                parsed: dict[str, Any] = response.json()
                self._raise_for_business_error(parsed)
                return parsed
            if response.status_code == _HTTP_INSUFFICIENT_CREDITS:
                raise KieInsufficientCreditsError(self._format_credit_error(response))
            if _HTTP_CLIENT_ERROR_START <= response.status_code < _HTTP_SERVER_ERROR_START:
                raise KieClientError(self._format_http_error(response))
            last_server_error = KieServerError(self._format_http_error(response))
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
        assert last_server_error is not None  # noqa: S101 (invariante: solo llega aquí tras 5xx)
        raise last_server_error

    @staticmethod
    def _raise_for_business_error(payload: dict[str, Any]) -> None:
        """Detecta `code: 402` (créditos insuficientes) en responses HTTP 200.

        Kie a veces devuelve 200 + body con un código de error embebido —
        especialmente en endpoints viejos. Lo levantamos como excepción
        para no dejar pasar errores silenciosos.
        """
        code = payload.get("code") if isinstance(payload, dict) else None
        if code == _HTTP_INSUFFICIENT_CREDITS:
            msg = payload.get("msg") or "saldo insuficiente"
            raise KieInsufficientCreditsError(f"Kie reportó code:402 — {msg}")

    def _raise_for_status(self, response: httpx.Response) -> None:
        """Como `response.raise_for_status` pero usando la jerarquía del dominio."""
        if response.is_success:
            return
        if response.status_code == _HTTP_INSUFFICIENT_CREDITS:
            raise KieInsufficientCreditsError(self._format_credit_error(response))
        if response.status_code < _HTTP_SERVER_ERROR_START:
            raise KieClientError(self._format_http_error(response))
        raise KieServerError(self._format_http_error(response))

    @staticmethod
    def _format_http_error(response: httpx.Response) -> str:
        return (
            f"{response.request.method} {response.request.url} -> "
            f"HTTP {response.status_code} {response.reason_phrase}"
        )

    @staticmethod
    def _format_credit_error(response: httpx.Response) -> str:
        """Mensaje accionable para 402 con el `msg` del body si está disponible."""
        try:
            body = response.json()
            api_msg = body.get("msg") if isinstance(body, dict) else None
        except ValueError:
            api_msg = None
        suffix = f": {api_msg}" if api_msg else ""
        return f"Saldo insuficiente en Kie{suffix}. Cargá créditos en https://kie.ai/billing"

    @staticmethod
    def _extract_data(payload: dict[str, Any]) -> dict[str, Any]:
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise KieClientError(f"respuesta sin 'data': {payload!r}")
        return data
