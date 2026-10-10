"""Sign-in throttle: after MAX_FAILURES wrong passwords within WINDOW seconds,
from one address or for one username, further attempts are refused until the
window passes or the Admin unlocks the account."""

import threading
import time

MAX_FAILURES = 5
WINDOW = 15 * 60


class Throttle:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str) -> list[float]:
        cutoff = self._clock() - WINDOW
        kept = [t for t in self._fails.get(key, []) if t > cutoff]
        if kept:
            self._fails[key] = kept
        else:
            self._fails.pop(key, None)
        return kept

    def locked(self, *keys: str) -> bool:
        with self._lock:
            return any(len(self._recent(k)) >= MAX_FAILURES for k in keys)

    def fail(self, *keys: str) -> None:
        with self._lock:
            for k in keys:
                self._recent(k)
                self._fails.setdefault(k, []).append(self._clock())

    def clear(self, *keys: str) -> None:
        with self._lock:
            for k in keys:
                self._fails.pop(k, None)
