from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from goodmoneying_shared.live_capability import LiveCapabilityEvaluation

SafeOrderOutboxStatus = Literal["ready", "blocked"]
SafeOrderBlockedReason = Literal[
    "live_disabled",
    "permission_missing",
    "permission_not_ready",
    "permission_expired",
    "withdraw_permission_present",
    "kill_switch_armed",
]


@dataclass(frozen=True)
class UpbitApiPermissionReadiness:
    has_order_permission: bool
    has_order_read_permission: bool
    has_withdraw_permission: bool
    expires_at: datetime


@dataclass(frozen=True)
class UpbitSafeOrderOutboxDecision:
    status: SafeOrderOutboxStatus
    blocked_reason: SafeOrderBlockedReason | None
    can_enqueue: bool
    can_submit: bool


def evaluate_upbit_safe_order_outbox(
    live_capability: LiveCapabilityEvaluation,
    permission_readiness: UpbitApiPermissionReadiness | None,
    *,
    now: datetime,
    kill_switch_armed: bool = False,
) -> UpbitSafeOrderOutboxDecision:
    if live_capability.state != "live_enabled":
        return _blocked("live_disabled")
    if permission_readiness is None:
        return _blocked("permission_missing")
    if permission_readiness.expires_at <= now:
        return _blocked("permission_expired")
    if not (
        permission_readiness.has_order_permission
        and permission_readiness.has_order_read_permission
    ):
        return _blocked("permission_not_ready")
    if permission_readiness.has_withdraw_permission:
        return _blocked("withdraw_permission_present")
    if kill_switch_armed:
        return _blocked("kill_switch_armed")
    return UpbitSafeOrderOutboxDecision(
        status="ready",
        blocked_reason=None,
        can_enqueue=True,
        can_submit=False,
    )


def _blocked(reason: SafeOrderBlockedReason) -> UpbitSafeOrderOutboxDecision:
    return UpbitSafeOrderOutboxDecision(
        status="blocked",
        blocked_reason=reason,
        can_enqueue=False,
        can_submit=False,
    )
