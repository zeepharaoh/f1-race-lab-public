"""Shared controls for hosted dashboards."""
from math import ceil, isfinite
from threading import Lock
from time import monotonic


class RefreshGate:
    def __init__(self, interval=60):
        self.interval = float(interval)
        if not isfinite(self.interval) or self.interval <= 0:
            raise ValueError('Refresh interval must be positive')
        self._next_allowed = None
        self._lock = Lock()

    def request(self, now=None):
        with self._lock:
            current = monotonic() if now is None else float(now)
            if not isfinite(current):
                raise ValueError('Refresh time must be finite')
            if self._next_allowed is not None and current < self._next_allowed:
                return ceil(self._next_allowed - current)
            self._next_allowed = current + self.interval
            return 0
