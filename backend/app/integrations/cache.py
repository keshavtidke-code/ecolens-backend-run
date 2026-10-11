"""Tiny thread-safe in-memory TTL cache (single process; enough to spare free APIs)."""
from __future__ import annotations

import threading
import time
from typing import Any, Hashable


class TTLCache:
    def __init__(self, ttl_s: float, maxsize: int = 1024) -> None:
        self.ttl_s = ttl_s
        self.maxsize = maxsize
        self._data: dict[Hashable, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: Hashable) -> Any | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at < time.monotonic():
                del self._data[key]
                return None
            return value

    def set(self, key: Hashable, value: Any) -> None:
        if self.ttl_s <= 0:
            return
        with self._lock:
            if len(self._data) >= self.maxsize:
                self._data.pop(next(iter(self._data)))  # evict oldest inserted
            self._data[key] = (time.monotonic() + self.ttl_s, value)
