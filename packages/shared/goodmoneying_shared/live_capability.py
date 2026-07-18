from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

LiveCapabilityState = Literal["live_disabled", "live_enabled"]
LiveCapabilityReason = Literal[
    "authority_missing",
    "authority_unavailable",
    "explicitly_disabled",
    "deployment_sha_mismatch",
    "approval_expired",
    "live_enabled",
]


@dataclass(frozen=True)
class LiveCapabilityRecord:
    state: LiveCapabilityState
    deployment_sha: str
    expires_at: datetime


@dataclass(frozen=True)
class LiveCapabilityEvaluation:
    state: LiveCapabilityState
    reason: LiveCapabilityReason


def evaluate_live_capability(
    record: LiveCapabilityRecord | None,
    *,
    deployment_sha: str,
    now: datetime,
) -> LiveCapabilityEvaluation:
    if record is None:
        return LiveCapabilityEvaluation("live_disabled", "authority_missing")
    if record.state != "live_enabled":
        return LiveCapabilityEvaluation("live_disabled", "explicitly_disabled")
    if record.deployment_sha != deployment_sha:
        return LiveCapabilityEvaluation("live_disabled", "deployment_sha_mismatch")
    if record.expires_at <= now:
        return LiveCapabilityEvaluation("live_disabled", "approval_expired")
    return LiveCapabilityEvaluation("live_enabled", "live_enabled")


def evaluate_live_capability_fail_closed(
    fetch_record: Callable[[], LiveCapabilityRecord | None],
    *,
    deployment_sha: str,
    now: datetime,
) -> LiveCapabilityEvaluation:
    try:
        record = fetch_record()
    except Exception:
        return LiveCapabilityEvaluation("live_disabled", "authority_unavailable")
    return evaluate_live_capability(record, deployment_sha=deployment_sha, now=now)


def fetch_global_live_capability_record(connection: Any) -> LiveCapabilityRecord | None:
    row = connection.execute(
        """
        SELECT state, deployment_sha, expires_at
        FROM trading_capabilities
        WHERE scope_type = 'global'
          AND scope_key = 'global'
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    return LiveCapabilityRecord(
        state=row["state"],
        deployment_sha=row["deployment_sha"],
        expires_at=row["expires_at"],
    )
