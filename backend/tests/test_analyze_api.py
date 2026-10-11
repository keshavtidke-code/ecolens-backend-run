"""POST /observations/{id}/analyze end to end (fake Gemini; real DB, storage and service)."""
import json
from datetime import timedelta
from pathlib import Path

import pytest

from app.ai.pipeline import PerceptionOutcome  # noqa: F401  (import check)
from app.core.exceptions import BadRequestError, GatewayTimeoutError
from app.core.settings import get_settings
from app.core.utils import utcnow
from app.db.session import SessionLocal
from app.models.analysis import Analysis
from app.models.observation import Observation
from tests.conftest import JPEG, VALID_PERCEPTION, register, upload

SPEC_FIELDS = [
    "ecosystem_type", "ecosystem_confidence", "visible_elements", "flora", "fauna", "water_presence",
    "soil_condition", "vegetation_density", "human_impact", "environmental_pressures",
    "interesting_observations", "confidence",
]


def analyze(client, headers, obs_id):
    return client.post(f"/api/v1/observations/{obs_id}/analyze", headers=headers)


@pytest.fixture
def obs(client, auth):
    return upload(client, auth, latitude="19.076", longitude="72.8777", user_note="after the rain").json()


def db_observation(obs_id):
    with SessionLocal() as db:
        return db.get(Observation, obs_id)


def test_analyze_stores_and_returns_the_perception(client, auth, gemini, obs):
    r = analyze(client, auth, obs["id"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["observation_id"] == obs["id"]
    assert body["habitat_type"] == "urban park" and body["habitat_confidence"] == 0.86
    assert body["model_name"] == "fake-gemini" and body["prompt_version"] == "perception-v1"
    assert list(body["perception_json"]) == SPEC_FIELDS
    assert body["perception_json"]["water_presence"] == "damp_ground"
    assert body["season_context"]

    # persisted in the analyses table (perception_json + extracted columns + telemetry)
    with SessionLocal() as db:
        row = db.get(Analysis, body["id"])
        assert row.perception_json == body["perception_json"]
        assert row.habitat_type == "urban park" and row.tokens_used == 1500 and row.latency_ms is not None
    # observation is complete and the detail view now exposes the analysis
    detail = client.get(f"/api/v1/observations/{obs['id']}", headers=auth).json()
    assert detail["status"] == "complete" and detail["analysis"]["id"] == body["id"]
    assert client.get(f"/api/v1/observations/{obs['id']}/status", headers=auth).json()["status"] == "complete"


def test_gemini_receives_photo_location_and_weather(client, auth, gemini, obs):
    analyze(client, auth, obs["id"])
    assert len(gemini.calls) == 1
    turn = gemini.calls[0]["turns"][0]
    assert turn.image.data == JPEG and turn.image.mime_type == "image/jpeg"
    assert "Mumbai, Maharashtra, India" in turn.text
    assert "temperature 31.5 C" in turn.text and "humidity 78%" in turn.text  # from the stored snapshot
    assert "after the rain" in turn.text
    assert "19.076" not in turn.text  # only coarse coordinates leave the server


def test_analyze_works_without_weather_or_location(client, auth, gemini, weather_provider, geocoder):
    from tests.test_weather_location_api import geo_error, wx_error

    weather_provider.error, geocoder.error = wx_error(), geo_error()
    obs = upload(client, auth).json()
    assert analyze(client, auth, obs["id"]).status_code == 200
    text = gemini.calls[0]["turns"][0].text
    assert "unknown place" in text and "Weather at that time: unavailable" in text


def test_invalid_json_is_retried_once_then_succeeds(client, auth, gemini, obs):
    gemini.replies = ["```not json```", json.dumps(VALID_PERCEPTION)]
    r = analyze(client, auth, obs["id"])
    assert r.status_code == 200 and len(gemini.calls) == 2
    with SessionLocal() as db:
        assert db.get(Analysis, r.json()["id"]).tokens_used == 3000


def test_two_invalid_replies_fail_cleanly_and_can_be_retried(client, auth, gemini, obs):
    gemini.replies = ["nope", "still nope"]
    r = analyze(client, auth, obs["id"])
    assert r.status_code == 502 and r.json()["code"] == "ai_invalid_output"
    assert len(gemini.calls) == 2
    status = client.get(f"/api/v1/observations/{obs['id']}/status", headers=auth).json()
    assert status["status"] == "failed" and status["failure_reason"].startswith("ai_invalid_output")
    with SessionLocal() as db:
        assert db.query(Analysis).count() == 0  # nothing half-saved

    gemini.calls.clear()
    gemini.replies = [json.dumps(VALID_PERCEPTION)]
    assert analyze(client, auth, obs["id"]).status_code == 200
    status = client.get(f"/api/v1/observations/{obs['id']}/status", headers=auth).json()
    assert status["status"] == "complete" and status["failure_reason"] is None


@pytest.mark.parametrize(
    "error,status,code",
    [
        (GatewayTimeoutError("slow", code="ai_provider_timeout"), 504, "ai_provider_timeout"),
        (BadRequestError("declined", code="analysis_blocked"), 400, "analysis_blocked"),
        (RuntimeError("bug"), 500, "internal_error"),
    ],
    ids=["timeout", "blocked", "unexpected"],
)
def test_provider_failures_mark_the_observation_failed(client, auth, gemini, obs, error, status, code):
    gemini.error = error
    r = analyze(client, auth, obs["id"])
    assert r.status_code == status and r.json()["code"] == code
    assert db_observation(obs["id"]).status == "failed"
    assert len(gemini.calls) == 1  # provider errors are never retried


def test_not_configured_is_503_and_leaves_state_untouched(client, auth, obs):
    r = analyze(client, auth, obs["id"])  # no `gemini` fixture => no key
    assert r.status_code == 503 and r.json()["code"] == "ai_not_configured"
    assert db_observation(obs["id"]).status == "pending"


def test_ownership_auth_and_missing(client, auth, gemini, obs):
    other = register(client, "b@example.com")
    assert analyze(client, other, obs["id"]).status_code == 404
    assert analyze(client, auth, 99999).status_code == 404
    assert client.post(f"/api/v1/observations/{obs['id']}/analyze").status_code == 401
    assert gemini.calls == []  # Gemini is never called for requests that fail these checks


def test_missing_photo_file_is_404(client, auth, gemini, obs):
    for f in Path(get_settings().storage_dir).rglob("*.jpg"):
        f.unlink()
    r = analyze(client, auth, obs["id"])
    assert r.status_code == 404 and r.json()["code"] == "photo_not_found"
    assert gemini.calls == [] and db_observation(obs["id"]).status == "pending"


def test_concurrent_analysis_is_rejected(client, auth, gemini, obs):
    with SessionLocal() as db:
        row = db.get(Observation, obs["id"])
        row.status, row.updated_at = "analyzing", utcnow()
        db.commit()
    r = analyze(client, auth, obs["id"])
    assert r.status_code == 409 and r.json()["code"] == "analysis_in_progress"
    assert gemini.calls == []


def test_stale_analyzing_status_can_be_rerun(client, auth, gemini, obs):
    with SessionLocal() as db:  # e.g. the server crashed mid-analysis 10 minutes ago
        row = db.get(Observation, obs["id"])
        row.status, row.updated_at = "analyzing", utcnow() - timedelta(minutes=10)
        db.commit()
    assert analyze(client, auth, obs["id"]).status_code == 200


def test_reanalysis_adds_a_newer_analysis_and_detail_shows_the_latest(client, auth, gemini, obs):
    first = analyze(client, auth, obs["id"]).json()
    gemini.replies = [json.dumps({**VALID_PERCEPTION, "ecosystem_type": "garden"})]
    second = analyze(client, auth, obs["id"]).json()
    assert second["id"] != first["id"]
    detail = client.get(f"/api/v1/observations/{obs['id']}", headers=auth).json()
    assert detail["analysis"]["id"] == second["id"] and detail["analysis"]["habitat_type"] == "garden"
    with SessionLocal() as db:
        assert db.query(Analysis).filter_by(observation_id=obs["id"]).count() == 2


def test_deleting_an_observation_removes_its_analyses(client, auth, gemini, obs):
    analyze(client, auth, obs["id"])
    assert client.delete(f"/api/v1/observations/{obs['id']}", headers=auth).status_code == 204
    with SessionLocal() as db:
        assert db.query(Analysis).count() == 0
