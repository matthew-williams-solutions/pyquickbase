"""A small time-limited cache for app, table, and field metadata. No network access."""

import threading
import time
from typing import Any


class SchemaCache:
    """Safe to share across threads, so one sync client can serve several of them."""

    def __init__(self, ttl: float) -> None:
        self._ttl = ttl
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any:
        """Return the cached value, or None if it's missing or expired."""
        with self._lock:
            entry = self._entries.get(key)
        if entry is None or time.monotonic() - entry[0] >= self._ttl:
            return None
        return entry[1]

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._entries[key] = (time.monotonic(), value)

    def delete(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._entries.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
