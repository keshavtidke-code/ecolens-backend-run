"""ASGI entry point for Supabase / Render deployment.

Previously this was a Firebase Cloud Functions Gen 2 adapter.
It has been replaced with a plain uvicorn-compatible ASGI entry so that the
backend can be deployed as a standard web service on Render (or any platform
that supports ASGI / WSGI apps).

Usage (Render / Procfile):
    uvicorn functions.main:app --host 0.0.0.0 --port $PORT

Or just use the backend directly (preferred):
    uvicorn app.main:app --host 0.0.0.0 --port $PORT
"""
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

# Re-export the FastAPI ASGI application so this module can also be used as an
# entry point: `uvicorn functions.main:app`
from app.main import app  # noqa: E402  # re-exported ASGI app

__all__ = ["app"]
