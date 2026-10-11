"""Gemini client (REST `generateContent`, backend-only). No SDK: httpx is already a dependency.

Why generateContent: Google documents it as fully supported, it is stateless (photos are not stored
server-side as with the Interactions API's default), and multi-turn retry history is explicit.
"""
from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass
from typing import Any, Literal, Optional, Protocol, Sequence

import httpx

from app.core.exceptions import BadGatewayError, BadRequestError, GatewayTimeoutError
from app.core.settings import get_settings

logger = logging.getLogger(__name__)

_BLOCKED_FINISH = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "IMAGE_SAFETY", "RECITATION"}


@dataclass(frozen=True)
class ImagePart:
    data: bytes
    mime_type: str


@dataclass(frozen=True)
class Turn:
    role: Literal["user", "model"]
    text: str
    image: Optional[ImagePart] = None


@dataclass(frozen=True)
class GeminiResult:
    text: str
    model: str
    prompt_tokens: Optional[int]
    output_tokens: Optional[int]
    total_tokens: Optional[int]
    finish_reason: Optional[str]
    latency_ms: int


class AIClient(Protocol):
    model: str

    def generate_json(
        self,
        *,
        system_prompt: str,
        turns: Sequence[Turn],
        response_schema: dict[str, Any],
        timeout_s: Optional[float] = None,
    ) -> GeminiResult: ...


class GeminiClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout_s: float,
        max_output_tokens: int = 8192,
        temperature: Optional[float] = None,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self.model = model
        self._url = f"{base_url.rstrip('/')}/models/{model}:generateContent"
        self._max_output_tokens = max_output_tokens
        self._temperature = temperature
        # The key travels in a header, never in the URL, so it cannot leak through URL logging.
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout_s, connect=5.0),
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            transport=transport,
        )

    # ---- public ----
    def generate_json(
        self,
        *,
        system_prompt: str,
        turns: Sequence[Turn],
        response_schema: dict[str, Any],
        timeout_s: Optional[float] = None,
    ) -> GeminiResult:
        """`timeout_s` overrides the client-wide timeout for this one call (used by chat deadlines)."""
        body = self.build_request(system_prompt, turns, response_schema)
        images = sum(1 for t in turns if t.image)
        image_bytes = sum(len(t.image.data) for t in turns if t.image)
        logger.info(
            "gemini request: model=%s turns=%d images=%d image_bytes=%d",
            self.model, len(turns), images, image_bytes,
        )
        started = time.perf_counter()
        data = self._post(body, timeout_s)
        latency_ms = int((time.perf_counter() - started) * 1000)
        result = self._parse(data, latency_ms)
        logger.info(
            "gemini response: finish=%s tokens(prompt/out/total)=%s/%s/%s model_version=%s latency=%d ms chars=%d",
            result.finish_reason, result.prompt_tokens, result.output_tokens, result.total_tokens,
            data.get("modelVersion"), latency_ms, len(result.text),
        )
        return result

    def close(self) -> None:
        self._client.close()

    # ---- request ----
    def build_request(
        self, system_prompt: str, turns: Sequence[Turn], response_schema: dict[str, Any]
    ) -> dict[str, Any]:
        contents: list[dict[str, Any]] = []
        for turn in turns:
            parts: list[dict[str, Any]] = []
            if turn.image:  # image first, then text (Google's recommended ordering)
                parts.append(
                    {
                        "inlineData": {
                            "mimeType": turn.image.mime_type,
                            "data": base64.b64encode(turn.image.data).decode("ascii"),
                        }
                    }
                )
            parts.append({"text": turn.text})
            contents.append({"role": turn.role, "parts": parts})
        generation_config: dict[str, Any] = {
            "responseMimeType": "application/json",
            "responseSchema": response_schema,
            "maxOutputTokens": self._max_output_tokens,
        }
        if self._temperature is not None:
            generation_config["temperature"] = self._temperature
        return {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": contents,
            "generationConfig": generation_config,
        }

    def _post(self, body: dict[str, Any], timeout_s: Optional[float] = None) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            if timeout_s is None:
                response = self._client.post(self._url, json=body)
            else:
                response = self._client.post(
                    self._url, json=body, timeout=httpx.Timeout(timeout_s, connect=min(5.0, timeout_s))
                )
        except httpx.TimeoutException:
            logger.warning("gemini timed out after %.1fs", time.perf_counter() - started)
            raise GatewayTimeoutError("The AI service timed out.", code="ai_provider_timeout") from None
        except httpx.HTTPError as exc:
            logger.warning("gemini unreachable: %s", type(exc).__name__)
            raise BadGatewayError("The AI service is unreachable.", code="ai_provider_error") from None

        status = response.status_code
        if status >= 400:
            upstream = self._upstream_message(response)
            logger.warning("gemini returned HTTP %d: %s", status, upstream)
            if status in (401, 403) or (status == 400 and "api key" in upstream.lower()):
                raise BadGatewayError("The AI service rejected our credentials.", code="ai_provider_auth_error")
            if status == 404:
                raise BadGatewayError(
                    "The configured AI model was not found.", code="ai_model_not_found"
                )
            if status == 429:
                raise BadGatewayError(
                    "The AI service is rate limiting requests; try again shortly.",
                    code="ai_provider_rate_limited",
                )
            raise BadGatewayError(
                f"The AI service returned an error (HTTP {status}).", code="ai_provider_error"
            )
        try:
            data = response.json()
        except ValueError:
            logger.warning("gemini returned a non-JSON body")
            raise BadGatewayError("The AI service returned an invalid response.", code="ai_provider_error") from None
        if not isinstance(data, dict):
            raise BadGatewayError("The AI service returned an invalid response.", code="ai_provider_error")
        return data

    @staticmethod
    def _upstream_message(response: httpx.Response) -> str:
        try:
            return str(response.json()["error"]["message"])[:300]
        except Exception:
            return response.text[:300]

    # ---- response ----
    def _parse(self, data: dict[str, Any], latency_ms: int) -> GeminiResult:
        feedback = data.get("promptFeedback") or {}
        if feedback.get("blockReason"):
            logger.warning("gemini blocked the prompt: %s", feedback["blockReason"])
            raise BadRequestError(
                "The AI declined to analyze this image.",
                code="analysis_blocked",
                details={"reason": str(feedback["blockReason"])},
            )
        candidates = data.get("candidates") or []
        candidate = candidates[0] if candidates and isinstance(candidates[0], dict) else {}
        finish = candidate.get("finishReason")
        if finish in _BLOCKED_FINISH:
            logger.warning("gemini stopped generation: %s", finish)
            raise BadRequestError(
                "The AI declined to analyze this image.",
                code="analysis_blocked",
                details={"reason": str(finish)},
            )
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(
            p["text"] for p in parts if isinstance(p, dict) and isinstance(p.get("text"), str) and not p.get("thought")
        )
        usage = data.get("usageMetadata") or {}
        return GeminiResult(
            text=text,
            model=self.model,
            prompt_tokens=usage.get("promptTokenCount"),
            output_tokens=usage.get("candidatesTokenCount"),
            total_tokens=usage.get("totalTokenCount"),
            finish_reason=finish,
            latency_ms=latency_ms,
        )


_client: Optional[GeminiClient] = None


def get_gemini_client() -> Optional[GeminiClient]:
    """Process-wide client, or None when GEMINI_API_KEY is not set (analysis then returns 503)."""
    global _client
    settings = get_settings()
    if settings.gemini_api_key is None:
        return None
    if _client is None:
        _client = GeminiClient(
            api_key=settings.gemini_api_key.get_secret_value(),
            model=settings.gemini_model,
            base_url=settings.gemini_base_url,
            timeout_s=settings.gemini_timeout_s,
            max_output_tokens=settings.gemini_max_output_tokens,
            temperature=settings.gemini_temperature,
        )
    return _client


def close_ai_client() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None
