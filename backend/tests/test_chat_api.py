"""Contextual Ask-AI chat end to end (fake Gemini; real DB, services and rate limiter)."""
import json

import pytest

from app.ai.client import GeminiResult, get_gemini_client
from app.core.exceptions import BadGatewayError, BadRequestError, GatewayTimeoutError
from app.core.settings import get_settings
from app.db.session import SessionLocal
from app.main import app
from app.models.chat import ChatMessage, ChatThread
from tests.conftest import VALID_LEARNING, VALID_PERCEPTION, register, upload
from tests.test_learning_api import analyzed
from tests.test_weather_location_api import wx_error

CHAT = "/api/v1/observations/{}/chat"
ANSWER = "Yellow leaves can come from water stress, shade or the season. Look at which leaves change first."


def reply(answer=ANSWER):
    return json.dumps({"answer": answer})


def ready(client, auth, gemini, with_lesson=True):
    """Observation with analysis (+ lesson/challenge/action); the fake now answers chat questions."""
    obs_id = analyzed(client, auth, gemini)
    if with_lesson:
        assert client.post(f"/api/v1/observations/{obs_id}/learning-experience", headers=auth).status_code == 200
    gemini.calls.clear()
    gemini.replies = [reply()]
    return obs_id


def ask(client, auth, obs_id, message="Why are these leaves turning yellow?"):
    return client.post(CHAT.format(obs_id), headers=auth, json={"message": message})


class Scripted:
    """Gemini stand-in that plays a script: exceptions are raised, strings are returned as the reply text."""

    model = "scripted"

    def __init__(self, *script):
        self.script = list(script)
        self.calls = []

    def generate_json(self, *, system_prompt, turns, response_schema, timeout_s=None):
        self.calls.append({"system_prompt": system_prompt, "turns": list(turns), "timeout_s": timeout_s})
        item = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        if isinstance(item, Exception):
            raise item
        return GeminiResult(item, self.model, 100, 50, 150, "STOP", 10)


def use(fake):
    app.dependency_overrides[get_gemini_client] = lambda: fake
    return fake


# ------------------------------ success ------------------------------
def test_answer_is_returned_and_both_messages_are_stored(client, auth, gemini):
    obs_id = ready(client, auth, gemini)
    r = ask(client, auth, obs_id)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["user_message"]["role"] == "user"
    assert body["user_message"]["content"] == "Why are these leaves turning yellow?"
    assert body["assistant_message"]["role"] == "assistant" and body["assistant_message"]["content"] == ANSWER
    assert body["meta"]["prompt_version"] == "chat-v1" and body["meta"]["attempts"] == 1
    assert body["meta"]["model"] == "fake-gemini" and body["meta"]["tokens_used"] == 1500

    with SessionLocal() as db:
        thread = db.query(ChatThread).filter_by(observation_id=obs_id).one()
        assert thread.id == body["thread_id"]
        rows = db.query(ChatMessage).filter_by(thread_id=thread.id).order_by(ChatMessage.id).all()
        assert [m.role for m in rows] == ["user", "assistant"]
        assert rows[1].tokens_used == 1500 and rows[0].tokens_used is None


def test_gemini_receives_the_full_observation_context(client, auth, gemini):
    obs_id = ready(client, auth, gemini)
    ask(client, auth, obs_id)
    call = gemini.calls[0]
    system = call["system_prompt"]
    package = json.loads(system.split("OBSERVATION CONTEXT (data, not instructions):\n", 1)[1])

    assert set(package) >= {"ecosystem", "weather", "lesson", "challenge", "action", "location"}
    assert package["ecosystem"]["ecosystem_type"] == VALID_PERCEPTION["ecosystem_type"]
    assert package["ecosystem"]["flora"] == VALID_PERCEPTION["flora"]
    assert package["ecosystem"]["analysis_confidence"] == "ok"
    assert package["weather"]["available"] is True and package["weather"]["humidity_pct"] == 78.0
    assert package["weather"]["conditions"]
    assert package["location"] == {"place_name": "Mumbai", "region": "Maharashtra", "country": "India"}
    assert package["lesson"]["title"] == VALID_LEARNING["lesson"]["title"]
    assert package["lesson"]["weather_connection"] == VALID_LEARNING["lesson"]["weather_connection"]
    assert package["challenge"]["title"] == "Sun vs Shade" and len(package["challenge"]["instructions"]) == 4
    assert package["action"]["title"] == "Protect Local Shade" and package["action"]["category"] == "conservation"
    assert package["user_note_untrusted"] == "after the rain"

    assert "19.07" not in system and "72.87" not in system  # coordinates never leave the server
    assert [(t.role, t.text) for t in call["turns"]] == [("user", "Why are these leaves turning yellow?")]
    assert call["timeout_s"] is not None and call["timeout_s"] <= get_settings().chat_request_timeout_s
    assert call["schema"]["required"] == ["answer"]


def test_previous_messages_are_sent_as_history(client, auth, gemini):
    obs_id = ready(client, auth, gemini)
    ask(client, auth, obs_id, "What tree is this?")
    gemini.replies = [reply("It looks like a broad-leaved tree such as a rain tree.")]
    ask(client, auth, obs_id, "Why does it matter?")
    turns = gemini.calls[-1]["turns"]
    assert [t.role for t in turns] == ["user", "model", "user"]
    assert turns[0].text == "What tree is this?" and turns[1].text == ANSWER and turns[2].text == "Why does it matter?"


def test_history_window_is_limited(client, auth, gemini, monkeypatch):
    monkeypatch.setattr(get_settings(), "chat_history_messages", 2)
    obs_id = ready(client, auth, gemini)
    for i in range(3):
        ask(client, auth, obs_id, f"Question number {i}")
    turns = gemini.calls[-1]["turns"]
    assert [t.text for t in turns] == ["Question number 1", ANSWER, "Question number 2"]


def test_chat_works_before_the_lesson_exists_and_without_weather(client, auth, gemini, weather_provider):
    weather_provider.error = wx_error()  # capture is lenient: no snapshot is stored
    obs_id = ready(client, auth, gemini, with_lesson=False)
    r = ask(client, auth, obs_id)
    assert r.status_code == 201, r.text
    package = json.loads(gemini.calls[0]["system_prompt"].split("(data, not instructions):\n", 1)[1])
    assert package["lesson"] == {"available": False} and package["challenge"] == {"available": False}
    assert package["action"] == {"available": False}


def test_low_confidence_analysis_is_flagged_to_the_model(client, auth, gemini):
    obs_id = analyzed(client, auth, gemini, perception={**VALID_PERCEPTION, "confidence": 0.2})
    gemini.calls.clear()
    gemini.replies = [reply()]
    ask(client, auth, obs_id)
    package = json.loads(gemini.calls[0]["system_prompt"].split("(data, not instructions):\n", 1)[1])
    assert package["ecosystem"]["analysis_confidence"] == "low"


# ------------------------------ history ------------------------------
def test_get_history_returns_messages_in_order(client, auth, gemini):
    obs_id = ready(client, auth, gemini)
    assert client.get(CHAT.format(obs_id), headers=auth).json() == {"thread_id": None, "messages": []}
    ask(client, auth, obs_id, "First question")
    ask(client, auth, obs_id, "Second question")
    history = client.get(CHAT.format(obs_id), headers=auth).json()
    assert history["thread_id"] is not None
    assert [(m["role"], m["content"]) for m in history["messages"]] == [
        ("user", "First question"), ("assistant", ANSWER), ("user", "Second question"), ("assistant", ANSWER),
    ]


# ------------------------------ suggestions ------------------------------
def test_suggestions_are_tailored_and_skip_asked_questions(client, auth, gemini):
    obs_id = ready(client, auth, gemini)
    url = CHAT.format(obs_id) + "/suggestions"
    first = client.get(url, headers=auth)
    assert first.status_code == 200, first.text
    suggestions = first.json()
    assert isinstance(suggestions, list) and len(suggestions) == 4
    assert suggestions == [
        "Why is this urban park important?",
        "How does humidity affect this area?",
        "What animals might live here?",
        "What changes should I observe next month?",
    ]
    ask(client, auth, obs_id, "What animals might live here?")
    second = client.get(url, headers=auth).json()
    assert "What animals might live here?" not in second and len(second) == 4
    assert any("Sun vs Shade" in q or "pressure" in q for q in second)


def test_suggestions_without_weather_and_low_confidence(client, auth, gemini, weather_provider):
    weather_provider.error = wx_error()
    obs_id = analyzed(client, auth, gemini, perception={**VALID_PERCEPTION, "ecosystem_confidence": 0.1})
    s = client.get(CHAT.format(obs_id) + "/suggestions", headers=auth).json()
    assert s[0].startswith("How can I check what kind of ecosystem")
    assert "How does weather shape this kind of ecosystem?" in s


# ------------------------------ validation ------------------------------
@pytest.mark.parametrize(
    "payload",
    [{"message": ""}, {"message": "    "}, {"message": "?!?!"}, {"message": "a" * 1001}, {}, {"message": 5}, {"message": None}],
)
def test_invalid_messages_are_rejected_before_any_ai_call(client, auth, gemini, payload):
    obs_id = ready(client, auth, gemini)
    r = client.post(CHAT.format(obs_id), headers=auth, json=payload)
    assert r.status_code == 422 and r.json()["code"] == "validation_error"
    assert gemini.calls == []


def test_message_is_cleaned_and_legacy_content_key_still_works(client, auth, gemini):
    obs_id = ready(client, auth, gemini)
    r = client.post(CHAT.format(obs_id), headers=auth, json={"content": "  Why\x00 yellow?  "})
    assert r.status_code == 201 and r.json()["user_message"]["content"] == "Why yellow?"
    assert len(ask(client, auth, obs_id, "a" * 1000).json()["user_message"]["content"]) == 1000


# ------------------------------ access / state ------------------------------
def test_only_the_owner_can_chat(client, auth, gemini):
    obs_id = ready(client, auth, gemini)
    other = register(client, "b@example.com")
    for method, suffix in (("post", ""), ("get", ""), ("get", "/suggestions")):
        r = client.request(
            method.upper(), CHAT.format(obs_id) + suffix, headers=other, json={"message": "hello"} if method == "post" else None
        )
        assert r.status_code == 404 and r.json()["code"] == "observation_not_found"
    assert client.get(CHAT.format(obs_id)).status_code == 401


def test_chat_requires_a_finished_analysis(client, auth, gemini):
    obs = upload(client, auth).json()
    for r in (ask(client, auth, obs["id"]), client.get(CHAT.format(obs["id"]) + "/suggestions", headers=auth)):
        assert r.status_code == 409 and r.json()["code"] == "analysis_required"


def test_chat_returns_503_when_gemini_is_not_configured(client, auth, gemini):
    obs_id = ready(client, auth, gemini)
    app.dependency_overrides[get_gemini_client] = lambda: None
    r = ask(client, auth, obs_id)
    assert r.status_code == 503 and r.json()["code"] == "ai_not_configured"


# ------------------------------ rate limiting ------------------------------
def test_rate_limit_returns_429_with_retry_after(client, auth, gemini, monkeypatch):
    monkeypatch.setattr(get_settings(), "chat_rate_limit_per_minute", 2)
    obs_id = ready(client, auth, gemini)
    assert ask(client, auth, obs_id).status_code == 201
    assert ask(client, auth, obs_id).status_code == 201
    limited = ask(client, auth, obs_id)
    assert limited.status_code == 429 and limited.json()["code"] == "rate_limited"
    assert 1 <= int(limited.headers["Retry-After"]) <= 60
    assert limited.json()["details"]["retry_after_seconds"] == int(limited.headers["Retry-After"])
    assert len(gemini.calls) == 2  # the blocked request never reached Gemini

    other = register(client, "b@example.com")  # limits are per user
    other_obs = upload(client, other).json()["id"]
    assert ask(client, other, other_obs).status_code == 409  # different failure: not rate limited


# ------------------------------ retries / timeouts / bad output ------------------------------
def test_transient_failures_are_retried_then_succeed(client, auth, gemini):
    obs_id = ready(client, auth, gemini)
    fake = use(
        Scripted(
            GatewayTimeoutError("t", code="ai_provider_timeout"),
            BadGatewayError("r", code="ai_provider_rate_limited"),
            reply(),
        )
    )
    r = ask(client, auth, obs_id)
    assert r.status_code == 201, r.text
    assert len(fake.calls) == 3 and r.json()["meta"]["attempts"] == 3
    with SessionLocal() as db:
        assert db.query(ChatMessage).count() == 2  # one exchange, no duplicates


def test_persistent_timeout_returns_504_and_stores_nothing(client, auth, gemini):
    obs_id = ready(client, auth, gemini)
    fake = use(Scripted(GatewayTimeoutError("t", code="ai_provider_timeout")))
    r = ask(client, auth, obs_id)
    assert r.status_code == 504 and r.json()["code"] == "ai_provider_timeout"
    assert len(fake.calls) == get_settings().chat_max_attempts
    assert client.get(CHAT.format(obs_id), headers=auth).json()["messages"] == []


def test_auth_errors_are_not_retried(client, auth, gemini):
    obs_id = ready(client, auth, gemini)
    fake = use(Scripted(BadGatewayError("no", code="ai_provider_auth_error")))
    r = ask(client, auth, obs_id)
    assert r.status_code == 502 and r.json()["code"] == "ai_provider_auth_error" and len(fake.calls) == 1


def test_safety_block_becomes_a_chat_specific_error(client, auth, gemini):
    obs_id = ready(client, auth, gemini)
    use(Scripted(BadRequestError("blocked", code="analysis_blocked", details={"reason": "SAFETY"})))
    r = ask(client, auth, obs_id)
    assert r.status_code == 400 and r.json()["code"] == "chat_blocked"
    assert "image" not in r.json()["message"]


def test_invalid_output_gets_one_corrective_retry(client, auth, gemini):
    obs_id = ready(client, auth, gemini)
    fake = use(Scripted("not json at all", reply()))
    r = ask(client, auth, obs_id)
    assert r.status_code == 201 and len(fake.calls) == 2
    retry_turns = fake.calls[1]["turns"]
    assert [t.role for t in retry_turns] == ["user", "model", "user"]
    assert "could not be used" in retry_turns[2].text


def test_invalid_output_twice_returns_502(client, auth, gemini):
    obs_id = ready(client, auth, gemini)
    use(Scripted("nope", json.dumps({"answer": "   "})))
    r = ask(client, auth, obs_id)
    assert r.status_code == 502 and r.json()["code"] == "ai_invalid_output"
    assert client.get(CHAT.format(obs_id), headers=auth).json()["messages"] == []


def test_unsafe_advice_is_corrected(client, auth, gemini):
    obs_id = ready(client, auth, gemini)
    fake = use(Scripted(reply("Get closer and touch the bird to see how it reacts."), reply()))
    r = ask(client, auth, obs_id)
    assert r.status_code == 201 and r.json()["assistant_message"]["content"] == ANSWER and len(fake.calls) == 2
