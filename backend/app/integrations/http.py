"""Shared HTTP helper that turns every provider failure into a consistent AppError."""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.core.exceptions import BadGatewayError, GatewayTimeoutError

logger = logging.getLogger(__name__)

# Number of attempts and backoff schedule for transient failures.
_MAX_ATTEMPTS = 3
_BACKOFF_BASE_S = 0.5  # 0.5 s → 1.0 s → give up


def get_json(client: httpx.Client, url: str, *, params: dict[str, Any], kind: str) -> Any:
    """GET `url` and return parsed JSON.

    `kind` ("weather" / "geocoding") names the provider in logs and error codes:
    `<kind>_provider_timeout` (504) and `<kind>_provider_error` (502).

    Automatically retries up to ``_MAX_ATTEMPTS`` times on transient errors
    (network timeouts, HTTP 429 / 5xx) using exponential back-off so that
    brief upstream hiccups don't surface as errors to mobile clients.
    """
    started = time.perf_counter()
    error_code = f"{kind}_provider_error"
    last_exc: Exception | None = None

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            
            response = client.get(url, params=params)
        except httpx.TimeoutException:
            elapsed = time.perf_counter() - started
            logger.warning(
                "%s provider timed out after %.1fs (attempt %d/%d)",
                kind, elapsed, attempt, _MAX_ATTEMPTS,
            )
            last_exc = GatewayTimeoutError(
                f"The {kind} service timed out.", code=f"{kind}_provider_timeout"
            )
            if attempt < _MAX_ATTEMPTS:
                time.sleep(_BACKOFF_BASE_S * (2 ** (attempt - 1)))
                continue
            raise last_exc from None
        except httpx.HTTPError as exc:
            logger.warning("%s provider unreachable: %s", kind, type(exc).__name__)
            last_exc = BadGatewayError(
                f"The {kind} service is unreachable.", code=error_code
            )
            if attempt < _MAX_ATTEMPTS:
                time.sleep(_BACKOFF_BASE_S * (2 ** (attempt - 1)))
                continue
            raise last_exc from None

        elapsed_ms = (time.perf_counter() - started) * 1000

        # Retry on rate-limiting or server errors.
        if response.status_code in (429, 500, 502, 503, 504):
            logger.warning(
                "%s provider returned HTTP %s (attempt %d/%d): %s",
                kind, response.status_code, attempt, _MAX_ATTEMPTS, response.text[:200],
            )
            last_exc = BadGatewayError(
                f"The {kind} service returned an error (HTTP {response.status_code}).",
                code=error_code,
            )
            if attempt < _MAX_ATTEMPTS:
                time.sleep(_BACKOFF_BASE_S * (2 ** (attempt - 1)))
                continue
            raise last_exc

        if response.status_code >= 400:
            logger.warning(
                "%s provider returned HTTP %s: %s",
                kind, response.status_code, response.text[:200],
            )
            raise BadGatewayError(
                f"The {kind} service returned an error (HTTP {response.status_code}).",
                code=error_code,
            )

        try:
            data = response.json()
        except ValueError:
            logger.warning("%s provider returned invalid JSON", kind)
            raise BadGatewayError(
                f"The {kind} service returned an invalid response.", code=error_code
            ) from None

        logger.debug("%s provider responded in %.0f ms", kind, elapsed_ms)
        return data

    # Should be unreachable, but satisfies type-checkers.
    raise last_exc  # type: ignore[misc]
