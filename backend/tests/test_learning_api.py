"""POST /observations/{id}/learning-experience end to end (fake Gemini; real DB and services)."""
import copy
import json

import pytest

from app.ai.client import get_gemini_client
from app.core.exceptions import GatewayTimeoutError
from app.db.session import SessionLocal
from app.main import app
from app.models.action import RecommendedAction
from app.models.analysis import Analysis
from app.models.challenge import Challenge
from app.models.lesson import Lesson
from app.models.observation import Observation
from tests.conftest import VALID_LEARNING, VALID_PERCEPTION, register, upload
from tests.test_weather_location_api import geo_error, wx_error

URL = "/api/v1/observations/{}/learning-experience"


def learn(client, headers, obs_id, body=None, **params):
    return client.post(URL.format(obs_id), headers=headers, json=body, params=params)


def count(model):
    with SessionLocal() as db:
        return db.query(model).count()


def analyzed(client, auth, gemini, perception=None, learning=None, **upload_kw):
    """Create an observation, run the perception stage, and queue the learning reply."""
    obs = upload(client, auth, latitude="19.076", longitude="72.8777", user_note="after the rain", **upload_kw).json()
    gemini.replies = [json.dumps(perception or VALID_PERCEPTION), json.dumps(learning or VALID_LEARNING)]
    assert client.post(f"/api/v1/observations/{obs['id']}/analyze", headers=auth).status_code == 200
    return obs["id"]


def last_prompt(gemini):
    return gemini.calls[-1]["turns"][0].text


# ------------------------------ success ------------------------------
def test_generates_lesson_challenge_and_action_in_the_spec_shape(client, auth, gemini):
    obs_id = analyzed(client, auth, gemini)
    r = learn(client, auth, obs_id)
    assert r.status_code == 200, r.text
    body = r.json()
    assert list(body) == ["lesson", "challenge", "recommended_action", "meta"]

    lesson = body["lesson"]
    assert lesson["title"] == "Why Trees Matter Here"
    for key in ["ecosystem_summary", "what_you_are_seeing", "why_it_matters", "weather_connection", "interesting_fact"]:
        assert lesson[key]
    assert lesson["key_concepts"] == ["urban heat", "shade", "transpiration"]

    challenge = body["challenge"]
    assert challenge["title"] == "Sun vs Shade" and challenge["estimated_minutes"] == 5
    assert len(challenge["instructions"]) == 4 and challenge["success_question"].startswith("Which spot")
    assert challenge["status"] == "open"

    action = body["recommended_action"]
    assert action["title"] == "Protect Local Shade"
    assert (action["category"], action["impact_level"], action["status"]) == ("conservation", "medium", "open")

    assert body["meta"]["prompt_version"] == "lesson-v1" and body["meta"]["model"] == "fake-gemini"
    assert body["meta"]["cautious_mode"] is False and body["meta"]["weather_available"] is True
    assert body["meta"]["reused"] is False


def test_results_are_saved_in_lesson_challenge_and_action_tables(client, auth, gemini):
    obs_id = analyzed(client, auth, gemini)
    body = learn(client, auth, obs_id).json()
    with SessionLocal() as db:
        analysis = db.query(Analysis).filter_by(observation_id=obs_id).one()
        lesson = db.get(Lesson, body["lesson"]["id"])
        assert lesson.analysis_id == analysis.id and lesson.difficulty_level == "beginner"
        assert lesson.prompt_version == "lesson-v1" and lesson.model_name == "fake-gemini"
        assert set(lesson.content_json) == {
            "ecosystem_summary", "what_you_are_seeing", "why_it_matters", "weather_connection", "interesting_fact"
        }
        assert lesson.key_concepts == ["urban heat", "shade", "transpiration"]
        challenge = db.get(Challenge, body["challenge"]["id"])
        assert challenge.checklist_json == VALID_LEARNING["challenge"]["instructions"]
        assert challenge.success_question and challenge.estimated_minutes == 5 and challenge.status == "open"
        action = db.get(RecommendedAction, body["recommended_action"]["id"])
        assert (action.category, action.impact_level, action.observation_id) == ("conservation", "medium", obs_id)


def test_observation_detail_exposes_the_stored_experience(client, auth, gemini):
    obs_id = analyzed(client, auth, gemini)
    learn(client, auth, obs_id)
    detail = client.get(f"/api/v1/observations/{obs_id}", headers=auth).json()
    assert detail["lesson"]["title"] == "Why Trees Matter Here"
    assert detail["challenges"][0]["success_question"] and detail["challenges"][0]["checklist_json"]
    assert detail["recommended_action"]["category"] == "conservation"


def test_gemini_receives_perception_place_weather_season_and_user_context(client, auth, gemini):
    obs_id = analyzed(client, auth, gemini)
    learn(client, auth, obs_id, {"knowledge_level": "intermediate", "interests": ["birds", "birds", " gardening "]})
    prompt = last_prompt(gemini)
    for fragment in ["Mumbai, Maharashtra, India", "temperature 31.5 C", "humidity 78%", "ecosystem_type: urban park",
                     "crows on the lawn", "knowledge level intermediate; interests: birds, gardening", "after the rain"]:
        assert fragment in prompt, fragment
    assert "latitude" not in prompt.lower() and "19.076" not in prompt
    assert gemini.calls[-1]["turns"][0].image is None            # text-only second stage
    assert len(gemini.calls) == 2                                # one perception call + one learning call


def test_works_with_no_body_and_with_empty_body(client, auth, gemini):
    obs_id = analyzed(client, auth, gemini)
    assert client.post(URL.format(obs_id), headers=auth).status_code == 200            # no body at all
    assert learn(client, auth, obs_id, {}, regenerate=True).status_code == 200         # empty JSON object


# ------------------------------ missing data ------------------------------
def test_missing_weather_does_not_prevent_the_lesson(client, auth, gemini, weather_provider, geocoder):
    weather_provider.error, geocoder.error = wx_error(), geo_error()
    obs = upload(client, auth).json()
    gemini.replies = [json.dumps(VALID_PERCEPTION), json.dumps(VALID_LEARNING)]
    client.post(f"/api/v1/observations/{obs['id']}/analyze", headers=auth)
    r = learn(client, auth, obs["id"])
    assert r.status_code == 200 and r.json()["meta"]["weather_available"] is False
    prompt = last_prompt(gemini)
    assert "Weather when the photo was taken: unavailable" in prompt and "Place: unknown place" in prompt


def test_no_previous_analysis_is_a_clear_409(client, auth, gemini):
    obs = upload(client, auth).json()
    r = learn(client, auth, obs["id"])
    assert r.status_code == 409 and r.json()["code"] == "analysis_required"
    assert f"/observations/{obs['id']}/analyze" in r.json()["message"]
    assert gemini.calls == []                                   # Gemini was never called
    assert count(Lesson) == count(Challenge) == count(RecommendedAction) == 0


def test_failed_analysis_without_a_result_is_also_analysis_required(client, auth, gemini):
    obs = upload(client, auth).json()
    gemini.replies = ["bad", "bad"]
    assert client.post(f"/api/v1/observations/{obs['id']}/analyze", headers=auth).status_code == 502
    assert learn(client, auth, obs["id"]).json()["code"] == "analysis_required"


def test_analysis_still_running_is_409(client, auth, gemini):
    obs_id = analyzed(client, auth, gemini)
    with SessionLocal() as db:
        db.get(Observation, obs_id).status = "analyzing"
        db.commit()
    r = learn(client, auth, obs_id)
    assert r.status_code == 409 and r.json()["code"] == "analysis_in_progress"


def test_corrupt_stored_analysis_is_409(client, auth, gemini):
    obs_id = analyzed(client, auth, gemini)
    with SessionLocal() as db:
        db.query(Analysis).filter_by(observation_id=obs_id).one().perception_json = {"unexpected": True}
        db.commit()
    r = learn(client, auth, obs_id)
    assert r.status_code == 409 and r.json()["code"] == "analysis_invalid"


def test_not_found_ownership_and_auth(client, auth, gemini):
    obs_id = analyzed(client, auth, gemini)
    other = register(client, "b@example.com")
    assert learn(client, auth, 99999).status_code == 404
    assert learn(client, other, obs_id).status_code == 404
    assert client.post(URL.format(obs_id)).status_code == 401
    assert len(gemini.calls) == 1                                # only the earlier analyze call


def test_request_body_validation(client, auth, gemini):
    obs_id = analyzed(client, auth, gemini)
    assert learn(client, auth, obs_id, {"knowledge_level": "wizard"}).status_code == 422
    assert learn(client, auth, obs_id, {"interests": ["a", "b", "c", "d", "e", "f"]}).status_code == 422


# ------------------------------ Gemini failures ------------------------------
def test_invalid_json_twice_fails_cleanly_and_saves_nothing(client, auth, gemini):
    obs_id = analyzed(client, auth, gemini)
    gemini.replies = [json.dumps(VALID_PERCEPTION), "nope", "still nope"]
    r = learn(client, auth, obs_id)
    assert r.status_code == 502 and r.json()["code"] == "ai_invalid_output"
    assert len(gemini.calls) == 3                                # analyze + exactly two learning attempts
    assert count(Lesson) == count(Challenge) == count(RecommendedAction) == 0


def test_invalid_json_is_retried_once_then_saved(client, auth, gemini):
    obs_id = analyzed(client, auth, gemini)
    gemini.replies = [json.dumps(VALID_PERCEPTION), "```not json```", json.dumps(VALID_LEARNING)]
    r = learn(client, auth, obs_id)
    assert r.status_code == 200 and len(gemini.calls) == 3
    retry_turns = gemini.calls[2]["turns"]
    assert [t.role for t in retry_turns] == ["user", "model", "user"] and "ONLY a single valid JSON" in retry_turns[2].text
    assert count(Lesson) == count(Challenge) == count(RecommendedAction) == 1   # saved once, not twice


def test_gemini_timeout_is_504_and_saves_nothing(client, auth, gemini):
    obs_id = analyzed(client, auth, gemini)
    gemini.error = GatewayTimeoutError("slow", code="ai_provider_timeout")
    r = learn(client, auth, obs_id)
    assert r.status_code == 504 and r.json()["code"] == "ai_provider_timeout"
    assert count(Lesson) == count(Challenge) == count(RecommendedAction) == 0
    assert client.get(f"/api/v1/observations/{obs_id}/status", headers=auth).json()["status"] == "complete"  # analysis untouched


def unsafe_learning(**challenge):
    data = copy.deepcopy(VALID_LEARNING)
    data["challenge"].update(challenge)
    return json.dumps(data)


def test_unsafe_challenge_is_never_saved(client, auth, gemini):
    obs_id = analyzed(client, auth, gemini)
    bad = unsafe_learning(instructions=["Catch a butterfly.", "Look closely.", "Release it."])
    gemini.replies = [json.dumps(VALID_PERCEPTION), bad, bad]
    r = learn(client, auth, obs_id)
    assert r.status_code == 502 and "safety" in r.json()["details"]["reason"]
    assert count(Challenge) == 0


def test_unsafe_first_reply_is_corrected_on_retry(client, auth, gemini):
    obs_id = analyzed(client, auth, gemini)
    bad = unsafe_learning(instructions=["Step into the pond.", "Look for fish.", "Step out again."])
    gemini.replies = [json.dumps(VALID_PERCEPTION), bad, json.dumps(VALID_LEARNING)]
    body = learn(client, auth, obs_id).json()
    assert body["challenge"]["title"] == "Sun vs Shade"
    assert count(Challenge) == 1


def test_not_configured_is_503_but_existing_results_are_still_served(client, auth, gemini):
    obs_id = analyzed(client, auth, gemini)
    first = learn(client, auth, obs_id).json()
    app.dependency_overrides.pop(get_gemini_client)                    # now "no API key"
    again = learn(client, auth, obs_id)
    assert again.status_code == 200 and again.json()["meta"]["reused"] is True
    assert again.json()["lesson"]["id"] == first["lesson"]["id"]
    assert learn(client, auth, obs_id, {"knowledge_level": "advanced"}).json()["code"] == "ai_not_configured"


# ------------------------------ low confidence ------------------------------
@pytest.mark.parametrize(
    "changes",
    [{"confidence": 0.3}, {"ecosystem_confidence": 0.4}, {"ecosystem_type": "unclear", "ecosystem_confidence": 0.9}],
    ids=["overall-low", "ecosystem-low", "unclear"],
)
def test_low_confidence_perception_makes_the_lesson_cautious(client, auth, gemini, changes):
    obs_id = analyzed(client, auth, gemini, perception={**VALID_PERCEPTION, **changes})
    body = learn(client, auth, obs_id).json()
    assert body["meta"]["cautious_mode"] is True
    prompt = last_prompt(gemini)
    assert "CAUTIOUS MODE" in prompt and "tentative wording" in prompt


def test_confident_perception_is_not_cautious(client, auth, gemini):
    obs_id = analyzed(client, auth, gemini)
    assert learn(client, auth, obs_id).json()["meta"]["cautious_mode"] is False
    assert "CAUTIOUS MODE" not in last_prompt(gemini)


def test_cautious_threshold_is_configurable(client, auth, gemini, monkeypatch):
    from app.core.settings import get_settings

    monkeypatch.setattr(get_settings(), "low_confidence_threshold", 0.9)   # 0.82 / 0.86 now count as low
    obs_id = analyzed(client, auth, gemini)
    assert learn(client, auth, obs_id).json()["meta"]["cautious_mode"] is True


def test_dangerous_weather_adds_safety_notes_to_the_prompt(client, auth, gemini, weather_provider):
    weather_provider.weather_code, weather_provider.temperature_c = 95, 38.0
    obs_id = analyzed(client, auth, gemini)
    learn(client, auth, obs_id)
    prompt = last_prompt(gemini)
    assert "SAFETY NOTES" in prompt and "thunderstorm" in prompt.lower() and "very hot" in prompt.lower()


# ------------------------------ reuse / regenerate ------------------------------
def test_second_call_reuses_the_stored_result_without_calling_gemini(client, auth, gemini):
    obs_id = analyzed(client, auth, gemini)
    first = learn(client, auth, obs_id).json()
    calls = len(gemini.calls)
    second = learn(client, auth, obs_id).json()
    assert len(gemini.calls) == calls
    assert second["meta"]["reused"] is True and second["lesson"]["id"] == first["lesson"]["id"]
    assert second["challenge"]["id"] == first["challenge"]["id"]
    assert count(Lesson) == 1


def test_regenerate_and_changed_level_create_new_records(client, auth, gemini):
    obs_id = analyzed(client, auth, gemini)
    first = learn(client, auth, obs_id).json()
    regenerated = learn(client, auth, obs_id, regenerate=True).json()
    assert regenerated["lesson"]["id"] != first["lesson"]["id"] and regenerated["meta"]["reused"] is False
    advanced = learn(client, auth, obs_id, {"knowledge_level": "advanced"}).json()
    assert advanced["lesson"]["knowledge_level"] == "advanced"
    assert count(Lesson) == count(Challenge) == count(RecommendedAction) == 3
    detail = client.get(f"/api/v1/observations/{obs_id}", headers=auth).json()
    assert detail["lesson"]["id"] == advanced["lesson"]["id"]           # detail shows the latest


def test_reanalysis_invalidates_reuse(client, auth, gemini):
    obs_id = analyzed(client, auth, gemini)
    first = learn(client, auth, obs_id).json()
    gemini.replies = [json.dumps({**VALID_PERCEPTION, "ecosystem_type": "garden"}), json.dumps(VALID_LEARNING)]
    gemini.calls.clear()
    client.post(f"/api/v1/observations/{obs_id}/analyze", headers=auth)
    second = learn(client, auth, obs_id).json()
    assert second["meta"]["reused"] is False and second["lesson"]["id"] != first["lesson"]["id"]
    assert second["meta"]["analysis_id"] != first["meta"]["analysis_id"]
    assert "ecosystem_type: garden" in last_prompt(gemini)


def test_deleting_the_observation_removes_the_learning_records(client, auth, gemini):
    obs_id = analyzed(client, auth, gemini)
    learn(client, auth, obs_id)
    assert client.delete(f"/api/v1/observations/{obs_id}", headers=auth).status_code == 204
    assert count(Lesson) == count(Challenge) == count(RecommendedAction) == 0
