from __future__ import annotations

import re
import time
from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from datetime import datetime
from email.utils import parsedate_to_datetime
from math import ceil, isfinite
from threading import Lock
from typing import Any, cast

from goodmoneying_upbit_gateway.catalog import load_catalog


def rate_limits_from_catalog(catalog: Mapping[str, Any]) -> dict[str, tuple[int, float]]:
    """REST endpoint가 참조하는 요청 제한을 카탈로그 단일 기준에서 만든다."""
    endpoints = cast(list[dict[str, Any]], catalog["rest_endpoints"])
    specifications = cast(dict[str, dict[str, Any]], catalog["rate_limits"])
    groups = {cast(str, endpoint["rate_limit_group"]) for endpoint in endpoints}
    return {
        group: (
            cast(int, specifications[group]["requests"]),
            float(specifications[group]["seconds"]),
        )
        for group in groups
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


def parse_penalty_seconds(
    retry_after: str | None,
    body: Any,
    *,
    now: Callable[[], float] = time.time,
    fallback_seconds: float = 60.0,
) -> float:
    """418 응답의 헤더·JSON에서 가장 긴 차단 시간을 보수적으로 선택한다."""
    current = now()
    candidates: list[float] = []
    if retry_after is not None:
        candidate = _duration_or_deadline(retry_after, current)
        if candidate is not None:
            candidates.append(candidate)
    _collect_penalty_candidates(body, current, candidates)
    finite_candidates = [candidate for candidate in candidates if _positive_finite(candidate)]
    return float(ceil(max(finite_candidates))) if finite_candidates else fallback_seconds


_DURATION_KEYS = {
    "retry_after",
    "retry_after_seconds",
    "block_duration",
    "block_duration_seconds",
    "blocked_for",
    "remaining_seconds",
}
_DEADLINE_KEYS = {"blocked_until", "block_until", "retry_at"}
_DURATION_PATTERN = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>seconds?|secs?|minutes?|mins?|hours?|초|분|시간)",
    re.IGNORECASE,
)


def _collect_penalty_candidates(value: Any, current: float, candidates: list[float]) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized_key = re.sub(
                r"(?<=[a-z0-9])(?=[A-Z])", "_", str(key)
            ).replace("-", "_").lower()
            if normalized_key in _DURATION_KEYS:
                candidate = _duration_or_deadline(item, current)
                if candidate is not None:
                    candidates.append(candidate)
            elif normalized_key in _DEADLINE_KEYS:
                candidate = _deadline_seconds(item, current)
                if candidate is not None:
                    candidates.append(candidate)
            _collect_penalty_candidates(item, current, candidates)
    elif isinstance(value, list):
        for item in value:
            _collect_penalty_candidates(item, current, candidates)
    elif isinstance(value, str):
        for match in _DURATION_PATTERN.finditer(value):
            amount = float(match.group("value"))
            unit = match.group("unit").lower()
            multiplier = 3600.0 if unit in {"hour", "hours", "시간"} else 60.0 if unit in {
                "minute",
                "minutes",
                "min",
                "mins",
                "분",
            } else 1.0
            candidate = amount * multiplier
            if _positive_finite(candidate):
                candidates.append(candidate)


def _duration_or_deadline(value: Any, current: float) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        numeric_value = float(value)
        return numeric_value if _positive_finite(numeric_value) else None
    if not isinstance(value, str):
        return None
    try:
        seconds = float(value)
    except ValueError:
        return _deadline_seconds(value, current)
    return seconds if _positive_finite(seconds) else None


def _deadline_seconds(value: Any, current: float) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        deadline_value = float(value)
        if current > 0 and deadline_value > current * 100:
            deadline_value /= 1_000
        seconds = deadline_value - current
        return seconds if _positive_finite(seconds) else None
    if not isinstance(value, str):
        return None
    try:
        return _deadline_seconds(float(value), current)
    except ValueError:
        pass
    try:
        deadline = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            deadline = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    seconds = deadline.timestamp() - current
    return seconds if _positive_finite(seconds) else None


def _positive_finite(value: float) -> bool:
    return isfinite(value) and value > 0


class GroupRateLimiter:
    def __init__(
        self,
        *,
        limits: Mapping[str, tuple[int, float]] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._limits = dict(
            rate_limits_from_catalog(load_catalog()) if limits is None else limits
        )
        self._clock = clock
        self._sleep = sleep
        self._calls: dict[str, deque[float]] = defaultdict(deque)
        self._blocked_until: dict[str, float] = {}
        self._lock = Lock()

    def acquire(self, group: str) -> None:
        limit, window = self._limits[group]
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
            if second_quota == 0 and group in self._limits:
                self._blocked_until[group] = max(
                    self._blocked_until.get(group, 0.0), self._clock() + 1.0
                )

    def defer(self, group: str, seconds: float) -> None:
        with self._lock:
            self._blocked_until[group] = max(
                self._blocked_until.get(group, 0.0), self._clock() + seconds
            )
