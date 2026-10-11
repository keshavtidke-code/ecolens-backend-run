"""Firebase Cloud Functions Gen 2 — FastAPI (ASGI) entry point via Mangum."""
from __future__ import annotations

import os
import sys

# ---------------------------------------------------------------------------
# Path setup: allow `from app.xxx import ...` to resolve relative to backend/
# ---------------------------------------------------------------------------
_FUNCTIONS_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.dirname(_FUNCTIONS_DIR)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

# ---------------------------------------------------------------------------
# Firebase Admin SDK bootstrap (must happen before any other firebase_admin call)
# ---------------------------------------------------------------------------
import firebase_admin  # noqa: E402
from firebase_functions import https_fn  # noqa: E402

if not firebase_admin._apps:
    firebase_admin.initialize_app()

# ---------------------------------------------------------------------------
# FastAPI app + Mangum ASGI adapter
# ---------------------------------------------------------------------------
from mangum import Mangum  # noqa: E402

from app.main import app as fastapi_app  # noqa: E402

# Mangum wraps our FastAPI (ASGI) app so it can receive Cloud Functions requests.
# lifespan="off" avoids the async startup/shutdown lifecycle clashing with the
# synchronous Cloud Functions runtime.
_handler = Mangum(fastapi_app, lifespan="off")


# ---------------------------------------------------------------------------
# Cloud Function definition
# ---------------------------------------------------------------------------
@https_fn.on_request(
    region="us-central1",
    memory=https_fn.options.MemoryOption.MB_512,
    timeout_sec=60,
    min_instances=0,
    max_instances=10,
)
def ecolens_api(req: https_fn.Request) -> https_fn.Response:
    """Route all HTTP requests for the EcoLens API to the FastAPI application."""
    return _handler(req, None)  # Mangum adapts the Firebase request → ASGI
