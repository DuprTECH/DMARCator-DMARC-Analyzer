"""Tiny in-process cache for expensive aggregate views.

Data only changes when reports are ingested or DNS is re-checked, so cached results are
dropped by `invalidate()` from those code paths. The TTL bounds staleness when another
process (e.g. `python -m app.manage poll` from cron) ingests reports.
"""

import threading
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

_lock = threading.Lock()
_store: dict[tuple, tuple[int, float, object]] = {}
_version = 0
MAX_ENTRIES = 256


def invalidate():
    global _version
    with _lock:
        _version += 1
        _store.clear()


def cached(key: tuple, compute: Callable[[], T], ttl: float = 300) -> T:
    now = time.monotonic()
    with _lock:
        hit = _store.get(key)
        version = _version
    if hit and hit[0] == version and now - hit[1] < ttl:
        return hit[2]  # type: ignore[return-value]
    value = compute()
    with _lock:
        if version == _version:  # don't store results computed before an invalidation
            if len(_store) >= MAX_ENTRIES:
                _store.clear()
            _store[key] = (version, now, value)
    return value
