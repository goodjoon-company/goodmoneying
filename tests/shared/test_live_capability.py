from __future__ import annotations

from datetime import UTC, datetime, timedelta

from goodmoneying_shared.live_capability import (
    LiveCapabilityRecord,
    evaluate_live_capability,
    evaluate_live_capability_fail_closed,
)

NOW = datetime(2026, 7, 18, 15, tzinfo=UTC)
SHA = "a" * 40


def test_live_capability는_권위_행이_없으면_비활성이다() -> None:
    result = evaluate_live_capability(None, deployment_sha=SHA, now=NOW)

    assert result.state == "live_disabled"
    assert result.reason == "authority_missing"


def test_live_capability는_SHA와_만료를_폐쇄형으로_평가한다() -> None:
    record = LiveCapabilityRecord(
        state="live_enabled",
        deployment_sha=SHA,
        expires_at=NOW + timedelta(minutes=5),
    )

    assert evaluate_live_capability(record, deployment_sha=SHA, now=NOW).state == "live_enabled"
    assert (
        evaluate_live_capability(record, deployment_sha="b" * 40, now=NOW).reason
        == "deployment_sha_mismatch"
    )
    assert (
        evaluate_live_capability(record, deployment_sha=SHA, now=NOW + timedelta(minutes=6)).reason
        == "approval_expired"
    )


def test_live_capability는_명시_비활성을_비활성으로_닫는다() -> None:
    record = LiveCapabilityRecord(
        state="live_disabled",
        deployment_sha=SHA,
        expires_at=NOW + timedelta(minutes=5),
    )

    result = evaluate_live_capability(record, deployment_sha=SHA, now=NOW)

    assert result.state == "live_disabled"
    assert result.reason == "explicitly_disabled"


def test_live_capability는_조회_실패를_비활성으로_닫는다() -> None:
    def fetch_failure() -> LiveCapabilityRecord | None:
        raise RuntimeError("DB unavailable")

    result = evaluate_live_capability_fail_closed(
        fetch_failure,
        deployment_sha=SHA,
        now=NOW,
    )

    assert result.state == "live_disabled"
    assert result.reason == "authority_unavailable"
