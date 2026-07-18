from __future__ import annotations

from datetime import UTC, datetime, timedelta

from goodmoneying_shared.live_capability import LiveCapabilityEvaluation
from goodmoneying_shared.upbit_safe_order_adapter import (
    UpbitApiPermissionReadiness,
    evaluate_upbit_safe_order_outbox,
)

NOW = datetime(2026, 7, 18, 19, tzinfo=UTC)


def test_safe_order_outbox는_live_disabled이면_주문을_준비하지_않는다() -> None:
    result = evaluate_upbit_safe_order_outbox(
        LiveCapabilityEvaluation("live_disabled", "authority_missing"),
        UpbitApiPermissionReadiness(
            has_order_permission=True,
            has_order_read_permission=True,
            has_withdraw_permission=False,
            expires_at=NOW + timedelta(minutes=10),
        ),
        now=NOW,
    )

    assert result.status == "blocked"
    assert result.blocked_reason == "live_disabled"
    assert result.can_enqueue is False
    assert result.can_submit is False


def test_safe_order_outbox는_주문과_조회_권한이_모두_있고_출금_권한이_없어야_ready다() -> None:
    result = evaluate_upbit_safe_order_outbox(
        LiveCapabilityEvaluation("live_enabled", "live_enabled"),
        UpbitApiPermissionReadiness(
            has_order_permission=True,
            has_order_read_permission=True,
            has_withdraw_permission=False,
            expires_at=NOW + timedelta(minutes=10),
        ),
        now=NOW,
    )

    assert result.status == "ready"
    assert result.blocked_reason is None
    assert result.can_enqueue is True
    assert result.can_submit is False


def test_safe_order_outbox는_출금_권한이_있으면_폐쇄형으로_차단한다() -> None:
    result = evaluate_upbit_safe_order_outbox(
        LiveCapabilityEvaluation("live_enabled", "live_enabled"),
        UpbitApiPermissionReadiness(
            has_order_permission=True,
            has_order_read_permission=True,
            has_withdraw_permission=True,
            expires_at=NOW + timedelta(minutes=10),
        ),
        now=NOW,
    )

    assert result.status == "blocked"
    assert result.blocked_reason == "withdraw_permission_present"
    assert result.can_enqueue is False
    assert result.can_submit is False


def test_safe_order_outbox는_권한_만료와_kill_switch를_차단한다() -> None:
    expired = evaluate_upbit_safe_order_outbox(
        LiveCapabilityEvaluation("live_enabled", "live_enabled"),
        UpbitApiPermissionReadiness(
            has_order_permission=True,
            has_order_read_permission=True,
            has_withdraw_permission=False,
            expires_at=NOW,
        ),
        now=NOW,
    )
    killed = evaluate_upbit_safe_order_outbox(
        LiveCapabilityEvaluation("live_enabled", "live_enabled"),
        UpbitApiPermissionReadiness(
            has_order_permission=True,
            has_order_read_permission=True,
            has_withdraw_permission=False,
            expires_at=NOW + timedelta(minutes=10),
        ),
        now=NOW,
        kill_switch_armed=True,
    )

    assert expired.blocked_reason == "permission_expired"
    assert killed.blocked_reason == "kill_switch_armed"
