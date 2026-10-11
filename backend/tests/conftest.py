import os
import shutil
import tempfile

# Must be set before the app (and its cached settings) is imported.
_tmp = tempfile.mkdtemp(prefix="ecolens-tests-")
os.environ.update(
    {
        "ENVIRONMENT": "testing",
        "DATABASE_URL": f"sqlite:///{_tmp}/test.db",
        "STORAGE_DIR": f"{_tmp}/storage",
        "SECRET_KEY": "test-secret-key-test-secret-key-0123456789",
        "LOG_LEVEL": "WARNING",
        "GEMINI_API_KEY": "",  # blank => not configured; tests must opt in with the `gemini` fixture
        "CHAT_RETRY_BACKOFF_S": "0",  # no real sleeping in chat retry tests
    }
)

import json  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.models  # noqa: E402,F401
from app.db.base import Base  # noqa: E402
from app.core.rate_limit import get_chat_rate_limiter  # noqa: E402
from app.core.settings import get_settings  # noqa: E402
from app.ai.client import GeminiResult, get_gemini_client  # noqa: E402
from app.core.utils import utcnow  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.integrations.geocoding import Location  # noqa: E402
from app.integrations.providers import get_geocoding_provider, get_weather_provider  # noqa: E402
from app.integrations.weather import WeatherReading  # noqa: E402
from app.main import app  # noqa: E402

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


@pytest.fixture(autouse=True)
def _reset_chat_rate_limiter():
    get_chat_rate_limiter().reset()  # user ids restart at 1 for every test, so counters must not leak
    yield


@pytest.fixture(autouse=True)
def _schema():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)
    shutil.rmtree(get_settings().storage_dir, ignore_errors=True)


class FakeWeather:
    """Stands in for Open-Meteo. Set `.error` to simulate an outage."""

    def __init__(self):
        self.error = None
        self.temperature_c = 31.5
        self.weather_code = 3
        self.calls = []

    def fetch(self, latitude, longitude, at=None):
        self.calls.append((latitude, longitude, at))
        if self.error:
            raise self.error
        return WeatherReading(
            provider="open-meteo",
            observed_at=utcnow().replace(microsecond=0),
            temperature_c=self.temperature_c,
            feels_like_c=36.2,
            humidity_pct=78.0,
            precipitation_mm=0.4,
            wind_speed_kmh=12.3,
            wind_direction_deg=240.0,
            cloud_cover_pct=65.0,
            weather_code=self.weather_code,
            pressure_hpa=1008.5,
            raw={"time": "fake"},
        )


class FakeGeocoder:
    """Stands in for Nominatim. Set `.error` to simulate an outage."""

    def __init__(self):
        self.error = None
        self.location = Location("Mumbai", "Maharashtra", "India")
        self.calls = []

    def reverse(self, latitude, longitude):
        self.calls.append((latitude, longitude))
        if self.error:
            raise self.error
        return self.location


@pytest.fixture(autouse=True)
def weather_provider():
    fake = FakeWeather()
    app.dependency_overrides[get_weather_provider] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_weather_provider, None)


@pytest.fixture(autouse=True)
def geocoder():
    fake = FakeGeocoder()
    app.dependency_overrides[get_geocoding_provider] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_geocoding_provider, None)


VALID_PERCEPTION = {
    "ecosystem_type": "urban park",
    "ecosystem_confidence": 0.86,
    "visible_elements": ["paved footpath", "scattered clouds", "apartment block in background"],
    "flora": ["broad-leaved tree (likely rain tree)", "short mown grass", "flowering shrubs"],
    "fauna": ["crows on the lawn"],
    "water_presence": "damp_ground",
    "soil_condition": "moist",
    "vegetation_density": "moderate",
    "human_impact": ["concrete path", "buildings", "a plastic bottle near the bench"],
    "environmental_pressures": ["litter", "limited ground cover under the trees"],
    "interesting_observations": ["Leaf litter is collecting under the large tree", "Moss on the north side of the trunk"],
    "confidence": 0.82,
}


VALID_LEARNING = {
    "lesson": {
        "title": "Why Trees Matter Here",
        "ecosystem_summary": "This looks like an urban park, a green pocket inside a busy city where grass, trees and paths share the space.",
        "what_you_are_seeing": "In your photo you can see broad-leaved trees, short grass and a paved footpath, with apartment buildings behind them. The soil looks moist and the plants cover a moderate part of the ground, which gives small creatures like crows somewhere to feed.",
        "why_it_matters": "Trees and grass do more than look nice. Their leaves give shade and release water vapour, which cools the air around them, while their roots help soak up rain instead of letting it run off the concrete.",
        "weather_connection": "It was about 31 degrees with high humidity when you took this photo, so the shade under those trees likely felt noticeably cooler than the open path. Light rain earlier would also explain the damp ground.",
        "interesting_fact": "A single mature tree can release hundreds of litres of water vapour on a warm day, which is one reason tree-lined streets feel cooler than bare ones.",
        "key_concepts": ["urban heat", "shade", "transpiration"],
    },
    "challenge": {
        "title": "Sun vs Shade",
        "description": "Compare how it feels to stand in direct sunlight and under a tree, and notice what changes.",
        "instructions": [
            "Stand in a sunny spot on the public path for one minute.",
            "Notice how the air and the ground feel.",
            "Move under a tree and wait one minute.",
            "Compare the two spots.",
        ],
        "estimated_minutes": 5,
        "success_question": "Which spot felt cooler, and what do you think caused the difference?",
    },
    "recommended_action": {
        "title": "Protect Local Shade",
        "description": "Stay on paths and avoid trampling grass and young plants, so the park's cooling green cover stays healthy for everyone.",
        "category": "conservation",
        "impact_level": "medium",
    },
}


class FakeGemini:
    """Stands in for GeminiClient. `replies` are returned in order (last one repeats)."""

    model = "fake-gemini"

    def __init__(self):
        self.replies = [json.dumps(VALID_PERCEPTION)]
        self.error = None
        self.calls = []

    def generate_json(self, *, system_prompt, turns, response_schema, timeout_s=None):
        self.calls.append(
            {"system_prompt": system_prompt, "turns": list(turns), "schema": response_schema, "timeout_s": timeout_s}
        )
        if self.error:
            raise self.error
        text = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        return GeminiResult(
            text=text, model=self.model, prompt_tokens=1200, output_tokens=300,
            total_tokens=1500, finish_reason="STOP", latency_ms=420,
        )


@pytest.fixture
def gemini():
    fake = FakeGemini()
    app.dependency_overrides[get_gemini_client] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_gemini_client, None)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def register(client, email="a@example.com"):
    r = client.post(
        "/api/v1/auth/register", json={"email": email, "password": "supersecret1", "display_name": "A"}
    )
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['tokens']['access_token']}"}


@pytest.fixture
def auth(client):
    return register(client)


def upload(client, headers, data=JPEG, **form):
    form = {"latitude": "19.07", "longitude": "72.87", **form}
    return client.post(
        "/api/v1/observations", headers=headers, data=form, files={"photo": ("p.jpg", data, "image/jpeg")}
    )
