from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable
from threading import Lock

RATE_LIMITS: dict[str, tuple[int, float]] = {
    "market": (10, 1.0),
    "candle": (10, 1.0),
    "trade": (10, 1.0),
    "ticker": (10, 1.0),
    "orderbook": (10, 1.0),
    "default": (30, 1.0),
    "order": (8, 1.0),
    "order-test": (8, 1.0),
    "order-cancel-all": (1, 2.0),
    "origin": (1, 10.0),
}

def parse_remaining_req(value: str | None) -> tuple[str, int] | None:
    if value is None:
        return None
    fields = {
        key.strip(): item.strip()
        for component in value.split(";")
        if "=" in component
        for key, item in [component.split("=", maxsplit=1)]
    }
    group = fields.get("group")
    second_quota = fields.get("sec")
    if group is None or second_quota is None or not second_quota.isdigit():
        return None
    return group, int(second_quota)


class GroupRateLimiter:
    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._calls: dict[str, deque[float]] = defaultdict(deque)
        self._blocked_until: dict[str, float] = {}
        self._lock = Lock()

    def acquire(self, group: str) -> None:
        limit, window = RATE_LIMITS[group]
        calls = self._calls[group]
        while True:
            with self._lock:
                now = self._clock()
                blocked_until = self._blocked_until.get(group, now)
                if blocked_until > now:
                    wait_seconds = blocked_until - now
                else:
                    while calls and now - calls[0] >= window:
                        calls.popleft()
                    if len(calls) < limit:
                        calls.append(now)
                        return
                    wait_seconds = window - (now - calls[0])
            self._sleep(wait_seconds)

    def observe(self, value: str | None) -> None:
        with self._lock:
            remaining = parse_remaining_req(value)
            if remaining is None:
                return
            group, second_quota = remaining
            if second_quota == 0 and group in RATE_LIMITS:
                self._blocked_until[group] = self._clock() + 1.0

    def defer(self, group: str, seconds: float) -> None:
        with self._lock:
            self._blocked_until[group] = max(
                self._blocked_until.get(group, 0.0), self._clock() + seconds
            )
