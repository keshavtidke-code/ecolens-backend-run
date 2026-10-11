"""Small in-memory sliding-window rate limiter.

Process-local: with several workers each one keeps its own counters, so the effective limit is
`limit x workers`. Swap the storage for Redis if the API is scaled out.
"""
from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import Callable, Hashable

from app.core.exceptions import TooManyRequestsError


class SlidingWindowRateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._hits: dict[tuple[Hashable, int], deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: Hashable, *, limits: list[tuple[int, int]], what: str = "requests") -> None:
        """Record one hit for `key`. `limits` is a list of (max_hits, window_seconds); 0 disables a limit.

        Raises TooManyRequestsError (429 + Retry-After) when any window is full; a rejected call is not recorded.
        """
        active = [(n, w) for n, w in limits if n > 0 and w > 0]
        if not active:
            return
        now = self._clock()
        with self._lock:
            retry_after = 0.0
            queues = []
            for max_hits, window in active:
                queue = self._hits.setdefault((key, window), deque())
                while queue and queue[0] <= now - window:
                    queue.popleft()
                if len(queue) >= max_hits:
                    retry_after = max(retry_after, queue[0] + window - now)
                queues.append(queue)
            if retry_after > 0:
                seconds = max(1, math.ceil(retry_after))
                raise TooManyRequestsError(
                    f"You are sending {what} too quickly. Try again in {seconds} seconds.",
                    code="rate_limited",
                    details={"retry_after_seconds": seconds},
                    headers={"Retry-After": str(seconds)},
                )
            for queue in queues:
                queue.append(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


_chat_limiter = SlidingWindowRateLimiter()


def get_chat_rate_limiter() -> SlidingWindowRateLimiter:
    return _chat_limiter
