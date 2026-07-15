from __future__ import annotations

from typing import Literal

SafetyLevel = Literal["read", "test", "blocked"]


class PolicyBlocked(RuntimeError):
    pass


class SafetyPolicy:
    def ensure_upstream_allowed(self, safety: SafetyLevel) -> None:
        if safety not in {"read", "test"}:
            raise PolicyBlocked("비파괴 안전 정책에 따라 업비트 상향 호출을 차단했습니다.")
