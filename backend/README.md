# EcoLens API: backend foundation

FastAPI + SQLAlchemy 2 + SQLite + Alembic. Gemini and weather integrations are intentionally
Implemented: location + weather lookup (Open-Meteo,
OpenStreetMap Nominatim; no keys) the Gemini vision **perception stage** and the **learning-experience stage** (lesson + challenge + action); both need `GEMINI_API_KEY`.

## Quick start

Requires Python 3.11+. Run everything from the `backend/` directory.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env                 # then set SECRET_KEY
python -c "import secrets; print(secrets.token_urlsafe(48))"   # paste into .env

alembic upgrade head                 # create / update the database (run again after pulling: migration 0002 adds lessons/actions columns)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- Interactive docs: http://localhost:8000/docs (disabled when `ENVIRONMENT=production`)
- Liveness: `GET /health` - Readiness (checks DB): `GET /health/ready`
- Mobile device/emulator: use your machine's LAN IP (Android emulator: `10.0.2.2`) and add the
  Expo origin to `CORS_ORIGINS` if you test from a browser.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

## Migrations

```bash
alembic revision --autogenerate -m "describe change"   # review the generated file!
alembic upgrade head
alembic downgrade -1
alembic check                                          # fails if models drift from migrations
```

## Production notes

- Set `ENVIRONMENT=production`, a random `SECRET_KEY` (32+ chars), `LOG_FORMAT=json`.
  The app refuses to start in production with the default key.
- Run with several workers: `uvicorn app.main:app --workers 2` (SQLite WAL handles this; keep
  writes short). Put it behind a TLS-terminating reverse proxy.
- Back up `ecolens.db` and the `storage/` directory together.

## API overview (all under `/api/v1`)

| Area | Endpoints |
|---|---|
| auth | `POST /auth/register`, `/auth/login`, `/auth/anonymous`, `/auth/refresh`; `GET/PATCH/DELETE /auth/me` |
| observations | `POST /observations` (multipart: `photo`, `latitude`, `longitude`, ...), `GET /observations`, `GET/DELETE /observations/{id}`, `GET /observations/{id}/status`, `GET /observations/{id}/photo`, `POST /observations/{id}/refresh-context`, `POST /observations/{id}/analyze`, `POST /observations/{id}/learning-experience` |
| weather | `GET /weather/preview?lat=&lon=[&at=]` -> `{location, weather}` |
| journal | `POST/GET /journal`, `GET/PATCH/DELETE /journal/{id}` |
| chat | `POST/GET /observations/{id}/chat`, `GET /observations/{id}/chat/suggestions` (see *Ask-AI chat* below) |

Errors always look like `{"code", "message", "details", "request_id"}`.
Lists use cursor pagination: `?limit=20&cursor=<next_cursor>`.

## Weather + location

- On `POST /observations` the backend reverse-geocodes the coordinates (place, region, country) and
  fetches weather **for the photo's timestamp** (current conditions if recent, otherwise Open-Meteo
  hourly/archive data), saving them on the Observation / WeatherSnapshot. Both lookups run in parallel.
- Capture is **lenient**: if a provider is down or slow, the photo is still saved (fields stay empty) and
  you can retry with `POST /observations/{id}/refresh-context`.
- `GET /weather/preview` is **strict**: a provider failure returns `502`/`504` with code
  `weather_provider_error|timeout` or `geocoding_provider_error|timeout`.
- Results are cached in memory (weather ~1 km / 10 min, geocoding ~110 m / 24 h) and geocoding is
  throttled to 1 request/second per Nominatim policy. Set `GEOCODING_USER_AGENT` with a real contact.
- Only rounded (~1 km) coordinates are written to logs.

## Perception stage (Gemini vision)

`POST /api/v1/observations/{id}/analyze` loads the observation, its weather snapshot and place, sends
the photo plus context to Gemini, validates the JSON with Pydantic (one corrective retry if invalid),
stores an `Analysis` row (`perception_json`, habitat, confidence, tokens, latency) and returns it.

- Requires `GEMINI_API_KEY` (else `503 ai_not_configured`). Default model `gemini-3.8-flash`
  (`gemini-2.5-flash` and `gemini-2.0-flash` are shut down by Google).
- Uses REST `generateContent` (stateless; photos are not stored server-side by Google's API call).
- Synchronous, ~5-30 s. Status flow: `pending -> analyzing -> complete | failed`; poll
  `GET /observations/{id}/status`. A second concurrent call returns `409 analysis_in_progress`.
- Errors: `400 analysis_blocked`, `404`, `409`, `502 ai_invalid_output|ai_provider_error|...`,
  `503 ai_not_configured`, `504 ai_provider_timeout`.
- Privacy: only coarse (~11 km) coordinates, place name, weather, the photo and the user's note go to
  Google. Check Google's terms: on free API tiers, inputs may be used to improve Google's products;
  use a paid tier for real user photos.
- Prompt lives in `app/ai/prompts/perception_v1.py` (`PROMPT_VERSION` is stored on every analysis).
- Not built yet: per-user rate limiting / daily analysis quota (each call costs money).

## Learning experience (lesson + challenge + action)

`POST /api/v1/observations/{id}/learning-experience` (optional body
`{"knowledge_level": "beginner|intermediate|advanced", "interests": ["birds"]}`, optional `?regenerate=true`).

- Requires a completed analysis (`409 analysis_required` otherwise). Text-only Gemini call using the stored
  `perception_json` + place + weather + season (no photo, no coordinates are sent).
- Saves one `Lesson`, one `Challenge` (instructions in `checklist_json`, plus `success_question`) and one
  `RecommendedAction` in a single transaction; `GET /observations/{id}` then includes all three.
- Same knowledge level + same analysis => the stored result is returned (`meta.reused: true`, no AI cost).
  Use `regenerate=true` for a fresh one. A new analysis invalidates reuse.
- Missing weather never blocks it (the lesson says weather was unavailable). Low-confidence perception
  (`LOW_CONFIDENCE_THRESHOLD`, or ecosystem "unclear") switches the prompt to cautious mode (`meta.cautious_mode`).
- Safety is enforced twice: the prompt forbids touching wildlife, tasting plants, entering water, climbing,
  restricted areas, handling garbage/chemicals, damaging plants and traffic; `app/ai/safety.py` then scans the
  challenge/action text and an unsafe reply is rejected and retried once. Weather adds notes (thunderstorm,
  heavy rain, extreme heat/cold, strong wind, fog).
- Invalid JSON / failed validation is retried once, then `502 ai_invalid_output`; nothing is saved on failure.

## Ask-AI chat

`POST /observations/{id}/chat` with `{"message": "..."}` answers a question using that observation's photo
analysis, lesson, challenge, recommended action, weather snapshot and place (names only, never coordinates)
plus the last `CHAT_HISTORY_MESSAGES` messages of the thread. The lesson stage is optional: chat works as
soon as the photo analysis is done (`409 analysis_required` / `analysis_in_progress` otherwise).

- Question and answer are stored together in `chat_threads` / `chat_messages` **only when Gemini succeeds**.
- Messages: trimmed, control characters removed, 1-1000 characters, must contain a letter or number.
- Rate limit per user: `CHAT_RATE_LIMIT_PER_MINUTE` (10) and `CHAT_RATE_LIMIT_PER_HOUR` (100) -> `429 rate_limited`
  with `Retry-After`. The limiter is in-process memory; use a shared store (e.g. Redis) with multiple workers.
- Resilience: timeouts, 429s and 5xx from Gemini are retried (`CHAT_MAX_ATTEMPTS`, exponential backoff from
  `CHAT_RETRY_BACKOFF_S`), each attempt limited by `CHAT_REQUEST_TIMEOUT_S` and everything by
  `CHAT_TOTAL_TIMEOUT_S`. Auth/model errors are not retried. An invalid or unsafe answer gets one corrective retry.
- `GET /observations/{id}/chat/suggestions` returns up to 4 starter questions built from the observation
  (rule-based, no AI call); questions the user already asked are skipped.
