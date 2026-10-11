"""Pure unit tests: rate limiter, history shaping, retry deadline, suggestions."""
import pytest

from app.ai import chat_pipeline
from app.ai.chat_pipeline import ChatRunConfig, history_to_turns, run_chat
from app.ai.client import GeminiResult
from app.core.exceptions import BadGatewayError, GatewayTimeoutError, TooManyRequestsError
from app.core.rate_limit import SlidingWindowRateLimiter
from app.services.chat_suggestions import build_suggestions


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_rate_limiter_sliding_window():
    clock = Clock()
    limiter = SlidingWindowRateLimiter(clock)
    limits = [(2, 60), (3, 3600)]
    limiter.check("u", limits=limits)
    limiter.check("u", limits=limits)
    with pytest.raises(TooManyRequestsError) as exc:
        limiter.check("u", limits=limits)
    assert exc.value.status_code == 429 and exc.value.headers["Retry-After"] == "60"
    clock.now += 61  # minute window frees up; hour window has 2 of 3 used
    limiter.check("u", limits=limits)
    clock.now += 61
    with pytest.raises(TooManyRequestsError) as hourly:
        limiter.check("u", limits=limits)
    assert int(hourly.value.headers["Retry-After"]) > 3000
    limiter.check("someone-else", limits=limits)
    limiter.check("u", limits=[(0, 60)])  # 0 disables


def test_rejected_calls_are_not_counted():
    clock = Clock()
    limiter = SlidingWindowRateLimiter(clock)
    limiter.check("u", limits=[(1, 60)])
    for _ in range(5):
        with pytest.raises(TooManyRequestsError):
            limiter.check("u", limits=[(1, 60)])
    clock.now += 60.5
    limiter.check("u", limits=[(1, 60)])


def test_history_starts_with_user_alternates_and_drops_dangling_user():
    turns = history_to_turns(
        [("assistant", "a0"), ("user", "u1"), ("assistant", "a1"), ("user", "u2"), ("user", "u3"), ("assistant", "a3"), ("user", "dangling")]
    )
    assert [(t.role, t.text) for t in turns] == [("user", "u1"), ("model", "a1"), ("user", "u2\n\nu3"), ("model", "a3")]
    assert history_to_turns([]) == []


class Boom:
    model = "boom"

    def __init__(self, error):
        self.error, self.calls = error, 0

    def generate_json(self, **kwargs):
        self.calls += 1
        raise self.error


def test_retry_stops_when_backoff_would_pass_the_deadline(monkeypatch):
    slept = []
    monkeypatch.setattr(chat_pipeline, "_sleep", slept.append)
    fake = Boom(GatewayTimeoutError("t", code="ai_provider_timeout"))
    with pytest.raises(GatewayTimeoutError):
        run_chat(fake, context_package={}, history=[], question="q",
                 config=ChatRunConfig(max_attempts=5, backoff_s=10, request_timeout_s=5, total_timeout_s=3))
    assert fake.calls == 1 and slept == []


def test_per_attempt_timeout_never_exceeds_remaining_budget(monkeypatch):
    seen = []

    class Spy:
        model = "spy"

        def generate_json(self, *, timeout_s=None, **kwargs):
            seen.append(timeout_s)
            return GeminiResult('{"answer": "Fine."}', "spy", 1, 1, 2, "STOP", 1)

    run_chat(Spy(), context_package={}, history=[], question="q",
             config=ChatRunConfig(request_timeout_s=25, total_timeout_s=10))
    assert 1.0 <= seen[0] <= 10


def test_provider_rate_limit_error_is_retried(monkeypatch):
    monkeypatch.setattr(chat_pipeline, "_sleep", lambda s: None)
    fake = Boom(BadGatewayError("x", code="ai_provider_rate_limited"))
    with pytest.raises(BadGatewayError):
        run_chat(fake, context_package={}, history=[], question="q", config=ChatRunConfig(max_attempts=3, backoff_s=0))
    assert fake.calls == 3


PERCEPTION = {"flora": ["oak"], "environmental_pressures": ["litter"]}


def test_suggestion_rules():
    s = build_suggestions(ecosystem="pond", perception=PERCEPTION, weather={"humidity_pct": 80.0},
                          challenge_title=None, action_title=None, low_confidence=False)
    assert s == ["Why is this pond important?", "How does humidity affect this area?",
                 "What animals might live here?", "What changes should I observe next month?"]
    s2 = build_suggestions(ecosystem="unclear", perception={}, weather={"temperature_c": 20.0},
                           challenge_title="Shade check", action_title="Save water", low_confidence=True,
                           already_asked=["WHAT ANIMALS MIGHT LIVE HERE?"])
    assert len(s2) == 4 and "What animals might live here?" not in s2
    assert s2[0].startswith("How can I check") and "Why is this ecosystem important?" in s2
    assert "How does the temperature affect this area?" in s2
