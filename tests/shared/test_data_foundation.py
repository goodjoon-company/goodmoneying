from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from goodmoneying_shared.data_foundation import (
    CoverageEvidence,
    DurableJobState,
    MarketCatalogItem,
    MarketCollectionPolicySettings,
    build_default_krw_targets,
    can_claim_job,
    classify_coverage,
    internal_minute_candle_gaps,
)


def test_market_collection_policy_requires_utc_and_valid_ranges() -> None:
    valid = MarketCollectionPolicySettings(
        start_at=datetime(2025, 1, 1, tzinfo=UTC),
        data_types=("source_candle", "trade_event"),
        candle_unit="1m",
        retention_days=365,
        priority=321,
        continuous=False,
    )

    valid.validate(changed_at=datetime(2026, 1, 1, tzinfo=UTC))

    with pytest.raises(ValueError, match="UTC"):
        replace(valid, start_at=datetime(2025, 1, 1)).validate(
            changed_at=datetime(2026, 1, 1, tzinfo=UTC)
        )
    with pytest.raises(ValueError, match="최소 한 개"):
        replace(valid, data_types=()).validate(changed_at=datetime(2026, 1, 1, tzinfo=UTC))
    with pytest.raises(ValueError, match="시작 시각"):
        replace(valid, start_at=datetime(2027, 1, 1, tzinfo=UTC)).validate(
            changed_at=datetime(2026, 1, 1, tzinfo=UTC)
        )
    with pytest.raises(ValueError, match="우선순위"):
        replace(valid, priority=0).validate(changed_at=datetime(2026, 1, 1, tzinfo=UTC))
    with pytest.raises(ValueError, match="캔들 주기"):
        replace(valid, candle_unit=cast(Any, "1d")).validate(
            changed_at=datetime(2026, 1, 1, tzinfo=UTC)
        )


OBSERVED_AT = datetime(2026, 7, 17, tzinfo=UTC)


def test_default_krw_policy_builds_all_required_targets_from_2024() -> None:
    market = MarketCatalogItem(
        market_code="KRW-BTC",
        korean_name="비트코인",
        english_name="Bitcoin",
        market_warning="NONE",
        tradable=True,
    )

    targets = build_default_krw_targets(market, observed_at=OBSERVED_AT)

    assert [(target.data_type, target.candle_unit) for target in targets] == [
        ("source_candle", "1m"),
        ("trade_event", None),
        ("orderbook_snapshot", None),
        ("ticker_snapshot", None),
    ]
    assert {target.start_at for target in targets} == {datetime(2024, 1, 1, tzinfo=UTC)}
    assert all(target.continuous for target in targets)
    assert all(target.priority == 100 for target in targets)
    assert all(target.retention_days is None for target in targets)


def test_non_krw_market_is_not_implicitly_included() -> None:
    market = MarketCatalogItem(
        market_code="USDT-BTC",
        korean_name="비트코인",
        english_name="Bitcoin",
        market_warning="NONE",
        tradable=True,
    )

    assert build_default_krw_targets(market, observed_at=OBSERVED_AT) == []


def test_default_policy_rejects_naive_observation_time() -> None:
    market = MarketCatalogItem(
        market_code="KRW-BTC",
        korean_name="비트코인",
        english_name="Bitcoin",
        market_warning="NONE",
        tradable=True,
    )

    with pytest.raises(ValueError, match="UTC"):
        build_default_krw_targets(market, observed_at=datetime(2026, 7, 17))


@pytest.mark.parametrize(
    ("evidence", "expected"),
    [
        (CoverageEvidence(before_listing=True), "unavailable"),
        (CoverageEvidence(outside_source_retention=True), "unavailable"),
        (
            CoverageEvidence(
                source_row_count=1,
                manifest_checksum="sha256:abc",
            ),
            "available",
        ),
        (
            CoverageEvidence(
                request_succeeded=True,
                no_trade_corroborated=True,
            ),
            "no_trade",
        ),
        (CoverageEvidence(request_succeeded=True), "unverified"),
        (
            CoverageEvidence(
                after_trading_end=True,
                market_trading_resumed=True,
            ),
            "unverified",
        ),
        (
            CoverageEvidence(
                after_trading_end=True,
                market_trading_resumed=True,
                source_row_count=1,
                manifest_checksum="sha256:resumed",
            ),
            "available",
        ),
        (
            CoverageEvidence(
                attempted=True,
                retry_budget_exhausted=True,
            ),
            "missing",
        ),
        (CoverageEvidence(), "unverified"),
    ],
)
def test_coverage_requires_positive_source_evidence(
    evidence: CoverageEvidence,
    expected: str,
) -> None:
    assert classify_coverage(evidence) == expected


def test_empty_success_response_alone_never_means_no_trade() -> None:
    assert (
        classify_coverage(CoverageEvidence(request_succeeded=True, source_row_count=0))
        == "unverified"
    )


def test_successful_minute_candle_page_confirms_only_fully_bounded_internal_gaps() -> None:
    requested_start_at = datetime(2026, 7, 17, 0, 0, tzinfo=UTC)
    requested_end_at = requested_start_at + timedelta(minutes=5)

    gaps = internal_minute_candle_gaps(
        requested_start_at=requested_start_at,
        requested_end_at=requested_end_at,
        candle_starts=(
            requested_start_at + timedelta(minutes=1),
            requested_start_at + timedelta(minutes=3),
        ),
    )

    assert gaps == (
        (
            requested_start_at + timedelta(minutes=2),
            requested_start_at + timedelta(minutes=3),
        ),
    )


@pytest.mark.parametrize("candle_starts", [(), (datetime(2026, 7, 17, 0, 2, tzinfo=UTC),)])
def test_empty_or_single_candle_page_cannot_confirm_no_trade(
    candle_starts: tuple[datetime, ...],
) -> None:
    requested_start_at = datetime(2026, 7, 17, 0, 0, tzinfo=UTC)

    assert internal_minute_candle_gaps(
        requested_start_at=requested_start_at,
        requested_end_at=requested_start_at + timedelta(minutes=5),
        candle_starts=candle_starts,
    ) == ()


def test_expired_lease_can_be_reclaimed_but_active_or_completed_job_cannot() -> None:
    now = OBSERVED_AT

    assert can_claim_job(DurableJobState(status="pending"), now=now)
    assert can_claim_job(
        DurableJobState(
            status="running",
            lease_owner="dead-worker",
            lease_expires_at=now - timedelta(seconds=1),
        ),
        now=now,
    )
    assert not can_claim_job(
        DurableJobState(
            status="running",
            lease_owner="live-worker",
            lease_expires_at=now + timedelta(seconds=1),
        ),
        now=now,
    )
    assert not can_claim_job(DurableJobState(status="succeeded"), now=now)
