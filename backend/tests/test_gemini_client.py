"""GeminiClient against a mocked HTTP transport: request shape, parsing and error mapping."""
import base64
import json
import logging

import httpx
import pytest

from app.ai.client import GeminiClient, ImagePart, Turn
from app.core.exceptions import BadGatewayError, BadRequestError, GatewayTimeoutError

KEY = "AIza-test-secret-key"
SCHEMA = {"type": "OBJECT", "properties": {}}
IMAGE = ImagePart(b"\xff\xd8\xff\xe0fakejpeg", "image/jpeg")


def make_client(handler, **kwargs):
    return GeminiClient(
        api_key=KEY, model="gemini-test", base_url="https://gemini.test/v1beta", timeout_s=5,
        transport=httpx.MockTransport(handler), **kwargs,
    )


def ok(text='{"a": 1}', **extra):
    body = {
        "candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 200, "totalTokenCount": 1200},
        "modelVersion": "gemini-test-001",
        **extra,
    }
    return httpx.Response(200, json=body)


def call(client, turns=None):
    return client.generate_json(
        system_prompt="SYS", turns=turns or [Turn("user", "describe", IMAGE)], response_schema=SCHEMA
    )


def test_request_shape():
    seen = []

    def handler(request):
        seen.append(request)
        return ok()

    call(make_client(handler))
    request = seen[0]
    assert str(request.url) == "https://gemini.test/v1beta/models/gemini-test:generateContent"
    assert request.headers["x-goog-api-key"] == KEY and KEY not in str(request.url)
    body = json.loads(request.content)
    assert body["systemInstruction"]["parts"][0]["text"] == "SYS"
    parts = body["contents"][0]["parts"]
    assert body["contents"][0]["role"] == "user"
    assert parts[0]["inlineData"]["mimeType"] == "image/jpeg"                  # image first
    assert base64.b64decode(parts[0]["inlineData"]["data"]) == IMAGE.data
    assert parts[1] == {"text": "describe"}
    config = body["generationConfig"]
    assert config["responseMimeType"] == "application/json" and config["responseSchema"] == SCHEMA
    assert config["maxOutputTokens"] == 8192 and "temperature" not in config   # model default unless set


def test_temperature_only_sent_when_configured():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return ok()

    call(make_client(handler, temperature=0.2))
    assert seen[0]["generationConfig"]["temperature"] == 0.2


def test_multi_turn_history_is_sent_in_order():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return ok()

    turns = [Turn("user", "q", IMAGE), Turn("model", "bad"), Turn("user", "fix it")]
    call(make_client(handler), turns)
    assert [c["role"] for c in seen[0]["contents"]] == ["user", "model", "user"]


def test_response_parsing_skips_thought_parts_and_reads_usage():
    payload = {
        "candidates": [{
            "content": {"parts": [{"text": "thinking...", "thought": True}, {"text": '{"a":'}, {"text": " 1}"}]},
            "finishReason": "STOP",
        }],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5, "totalTokenCount": 15},
    }
    result = call(make_client(lambda r: httpx.Response(200, json=payload)))
    assert result.text == '{"a": 1}'
    assert (result.prompt_tokens, result.output_tokens, result.total_tokens) == (10, 5, 15)
    assert result.finish_reason == "STOP" and result.model == "gemini-test"


def test_no_candidates_returns_empty_text_so_the_pipeline_can_retry():
    result = call(make_client(lambda r: httpx.Response(200, json={"candidates": []})))
    assert result.text == ""


def test_timeout():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(GatewayTimeoutError) as exc:
        call(make_client(handler))
    assert exc.value.status_code == 504 and exc.value.code == "ai_provider_timeout"


def test_unreachable():
    def handler(request):
        raise httpx.ConnectError("dns", request=request)

    with pytest.raises(BadGatewayError) as exc:
        call(make_client(handler))
    assert exc.value.code == "ai_provider_error"


@pytest.mark.parametrize(
    "response,code",
    [
        (httpx.Response(500, text="boom"), "ai_provider_error"),
        (httpx.Response(503, json={"error": {"message": "overloaded"}}), "ai_provider_error"),
        (httpx.Response(429, json={"error": {"message": "quota"}}), "ai_provider_rate_limited"),
        (httpx.Response(401, json={"error": {"message": "unauthenticated"}}), "ai_provider_auth_error"),
        (httpx.Response(403, json={"error": {"message": "forbidden"}}), "ai_provider_auth_error"),
        (httpx.Response(400, json={"error": {"message": "API key not valid. Please pass a valid API key."}}), "ai_provider_auth_error"),
        (httpx.Response(400, json={"error": {"message": "bad schema"}}), "ai_provider_error"),
        (httpx.Response(404, json={"error": {"message": "model not found"}}), "ai_model_not_found"),
        (httpx.Response(200, text="<html>nope</html>"), "ai_provider_error"),
        (httpx.Response(200, json=["not", "a", "dict"]), "ai_provider_error"),
    ],
    ids=["500", "503", "429", "401", "403", "400-key", "400-other", "404", "bad-json", "wrong-shape"],
)
def test_http_errors_map_to_clean_codes(response, code):
    with pytest.raises(BadGatewayError) as exc:
        call(make_client(lambda r: response))
    assert exc.value.status_code == 502 and exc.value.code == code
    assert KEY not in exc.value.message  # never echo secrets


@pytest.mark.parametrize(
    "payload,reason",
    [
        ({"promptFeedback": {"blockReason": "SAFETY"}}, "SAFETY"),
        ({"candidates": [{"finishReason": "SAFETY"}]}, "SAFETY"),
        ({"candidates": [{"finishReason": "IMAGE_SAFETY"}]}, "IMAGE_SAFETY"),
        ({"candidates": [{"finishReason": "PROHIBITED_CONTENT"}]}, "PROHIBITED_CONTENT"),
    ],
)
def test_blocked_content_is_a_400_not_a_retry(payload, reason):
    with pytest.raises(BadRequestError) as exc:
        call(make_client(lambda r: httpx.Response(200, json=payload)))
    assert exc.value.code == "analysis_blocked" and exc.value.details == {"reason": reason}


def test_api_key_never_appears_in_logs(caplog):
    caplog.set_level(logging.DEBUG)
    call(make_client(lambda r: ok()))
    with pytest.raises(BadGatewayError):
        call(make_client(lambda r: httpx.Response(401, json={"error": {"message": "nope"}})))
    assert KEY not in caplog.text


def test_httpx_url_logging_is_silenced_so_exact_coordinates_cannot_leak():
    from app.main import create_app  # noqa: F401  (applies logging config)

    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
