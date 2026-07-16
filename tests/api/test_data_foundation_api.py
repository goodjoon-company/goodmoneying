from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from goodmoneying_api.main import create_app
from goodmoneying_shared.data_foundation import (
    DataFoundationOverview,
    MarketCollectionPolicySettings,
    MarketCollectionStatus,
)
from goodmoneying_shared.sqlite_repository import SQLiteOperationsRepository


def test_data_foundation_api_exposes_utc_policy_and_five_coverage_states() -> None:
    repository = FakeDataFoundationRepository()
    client = TestClient(
        create_app(
            SQLiteOperationsRepository(),
            data_foundation_repository=repository,
        )
    )

    response = client.get("/v1/data-foundation")

    assert response.status_code == 200
    payload = response.json()
    assert payload["timeZone"] == "UTC"
    assert payload["policyStartAt"] == "2024-01-01T00:00:00Z"
    assert payload["summary"] == {
        "marketCount": 2,
        "krwMarketCount": 1,
        "activeTargetCount": 4,
        "pendingBackfillJobCount": 1,
        "desiredSubscriptionCount": 3,
        "coverageCounts": {
            "available": 1,
            "no_trade": 2,
            "missing": 5,
            "unavailable": 3,
            "unverified": 4,
        },
    }
    assert payload["markets"][0]["marketCode"] == "KRW-BTC"
    assert payload["markets"][0]["targetStatus"] == "active"
    assert payload["markets"][0]["collectionPolicy"] == {
        "startAt": "2024-01-01T00:00:00Z",
        "dataTypes": [
            "source_candle",
            "trade_event",
            "orderbook_snapshot",
            "ticker_snapshot",
        ],
        "candleUnit": "1m",
        "retentionDays": None,
        "priority": 100,
        "continuous": True,
    }


def test_market_policy_state_change_requires_operator_token_and_reason() -> None:
    repository = FakeDataFoundationRepository()
    client = TestClient(
        create_app(
            SQLiteOperationsRepository(),
            data_foundation_repository=repository,
        )
    )

    unauthorized = client.patch(
        "/v1/data-foundation/markets/KRW-BTC",
        json={"state": "excluded", "reason": "운영자 제외"},
    )
    invalid = client.patch(
        "/v1/data-foundation/markets/KRW-BTC",
        headers={"X-Operator-Token": "local-dev-token"},
        json={"state": "excluded", "reason": ""},
    )
    accepted = client.patch(
        "/v1/data-foundation/markets/KRW-BTC",
        headers={"X-Operator-Token": "local-dev-token"},
        json={
            "requestId": "req-001",
            "idempotencyKey": "market-KRW-BTC-001",
            "actorId": "operator:goodjoon",
            "requestedAt": "2026-07-17T00:00:00Z",
            "state": "excluded",
            "reason": "운영자 제외",
        },
    )

    assert unauthorized.status_code == 401
    assert invalid.status_code == 422
    assert accepted.status_code == 200
    assert accepted.json()["marketCode"] == "KRW-BTC"
    assert accepted.json()["state"] == "excluded"
    assert repository.change == ("KRW-BTC", "excluded", "운영자 제외")
    assert repository.command == {
        "request_id": "req-001",
        "idempotency_key": "market-KRW-BTC-001",
        "actor": "operator:goodjoon",
        "requested_at": datetime(2026, 7, 17, tzinfo=UTC),
    }


def test_market_change_requires_command_envelope() -> None:
    client = TestClient(
        create_app(
            SQLiteOperationsRepository(),
            data_foundation_repository=FakeDataFoundationRepository(),
        )
    )

    response = client.patch(
        "/v1/data-foundation/markets/KRW-BTC",
        headers={"X-Operator-Token": "local-dev-token"},
        json={"state": "paused", "reason": "운영 중지"},
    )

    assert response.status_code == 422


def test_market_policy_can_be_updated_with_state_and_rejects_non_utc_start() -> None:
    repository = FakeDataFoundationRepository()
    client = TestClient(
        create_app(
            SQLiteOperationsRepository(),
            data_foundation_repository=repository,
        )
    )
    headers = {"X-Operator-Token": "local-dev-token"}

    invalid = client.patch(
        "/v1/data-foundation/markets/KRW-BTC",
        headers=headers,
        json={
            "requestId": "req-policy-invalid",
            "idempotencyKey": "policy-KRW-BTC-invalid",
            "actorId": "operator:goodjoon",
            "requestedAt": "2026-07-17T00:00:00Z",
            "state": "active",
            "reason": "정책 변경",
            "policy": {
                "startAt": "2025-01-01T09:00:00+09:00",
                "dataTypes": ["source_candle"],
                "candleUnit": "1m",
                "retentionDays": 365,
                "priority": 321,
                "continuous": False,
            },
        },
    )
    accepted = client.patch(
        "/v1/data-foundation/markets/KRW-BTC",
        headers=headers,
        json={
            "requestId": "req-policy-accepted",
            "idempotencyKey": "policy-KRW-BTC-accepted",
            "actorId": "operator:goodjoon",
            "requestedAt": "2026-07-17T00:00:00Z",
            "state": "active",
            "reason": "정책 변경",
            "policy": {
                "startAt": "2025-01-01T00:00:00Z",
                "dataTypes": ["source_candle", "trade_event"],
                "candleUnit": "1m",
                "retentionDays": 365,
                "priority": 321,
                "continuous": False,
            },
        },
    )

    assert invalid.status_code == 422
    assert accepted.status_code == 200
    assert repository.policy_change == {
        "start_at": datetime(2025, 1, 1, tzinfo=UTC),
        "data_types": ("source_candle", "trade_event"),
        "candle_unit": "1m",
        "retention_days": 365,
        "priority": 321,
        "continuous": False,
    }


def test_market_policy_requires_at_least_one_supported_data_type() -> None:
    repository = FakeDataFoundationRepository()
    client = TestClient(
        create_app(
            SQLiteOperationsRepository(),
            data_foundation_repository=repository,
        )
    )
    base: dict[str, Any] = {
        "state": "active",
        "reason": "정책 변경",
        "policy": {
            "startAt": "2025-01-01T00:00:00Z",
            "dataTypes": [],
            "candleUnit": "1m",
            "retentionDays": None,
            "priority": 100,
            "continuous": True,
        },
    }

    empty = client.patch(
        "/v1/data-foundation/markets/KRW-BTC",
        headers={"X-Operator-Token": "local-dev-token"},
        json=base,
    )
    base["policy"]["dataTypes"] = ["source_candle", "unsupported"]
    unsupported = client.patch(
        "/v1/data-foundation/markets/KRW-BTC",
        headers={"X-Operator-Token": "local-dev-token"},
        json=base,
    )
    base["policy"]["dataTypes"] = ["source_candle"]
    base["policy"]["candleUnit"] = "1d"
    unsupported_candle_unit = client.patch(
        "/v1/data-foundation/markets/KRW-BTC",
        headers={"X-Operator-Token": "local-dev-token"},
        json=base,
    )

    assert empty.status_code == 422
    assert unsupported.status_code == 422
    assert unsupported_candle_unit.status_code == 422


class FakeDataFoundationRepository:
    change: tuple[str, str, str] | None = None
    policy_change: dict[str, object] | None = None
    command: dict[str, object] | None = None

    def overview(self) -> DataFoundationOverview:
        counts = {
            "available": 1,
            "no_trade": 2,
            "missing": 5,
            "unavailable": 3,
            "unverified": 4,
        }
        return DataFoundationOverview(
            market_count=2,
            krw_market_count=1,
            active_target_count=4,
            pending_backfill_job_count=1,
            desired_subscription_count=3,
            policy_start_at=datetime(2024, 1, 1, tzinfo=UTC),
            coverage_counts=counts,  # type: ignore[arg-type]
            markets=[
                MarketCollectionStatus(
                    market_code="KRW-BTC",
                    korean_name="비트코인",
                    english_name="Bitcoin",
                    quote_currency="KRW",
                    trading_status="active",
                    market_warning="NONE",
                    target_status="active",
                    active_data_type_count=4,
                    total_data_type_count=4,
                    coverage_counts=counts,  # type: ignore[arg-type]
                    collection_policy=MarketCollectionPolicySettings(
                        start_at=datetime(2024, 1, 1, tzinfo=UTC),
                        data_types=(
                            "source_candle",
                            "trade_event",
                            "orderbook_snapshot",
                            "ticker_snapshot",
                        ),
                        candle_unit="1m",
                        retention_days=None,
                        priority=100,
                        continuous=True,
                    ),
                ),
                MarketCollectionStatus(
                    market_code="USDT-BTC",
                    korean_name="비트코인",
                    english_name="Bitcoin",
                    quote_currency="USDT",
                    trading_status="active",
                    market_warning="NONE",
                    target_status="not_targeted",
                    active_data_type_count=0,
                    total_data_type_count=0,
                    coverage_counts=counts,  # type: ignore[arg-type]
                    collection_policy=None,
                ),
            ],
        )

    def set_market_target_state(
        self,
        market_code: str,
        *,
        state: str,
        actor: str,
        reason: str,
        changed_at: datetime,
        request_id: str,
        idempotency_key: str,
        requested_at: datetime,
        policy: MarketCollectionPolicySettings | None = None,
    ) -> datetime:
        assert actor
        assert changed_at.tzinfo == UTC
        self.change = (market_code, state, reason)
        self.command = {
            "request_id": request_id,
            "idempotency_key": idempotency_key,
            "actor": actor,
            "requested_at": requested_at,
        }
        if policy is not None:
            self.policy_change = {
                "start_at": policy.start_at,
                "data_types": policy.data_types,
                "candle_unit": policy.candle_unit,
                "retention_days": policy.retention_days,
                "priority": policy.priority,
                "continuous": policy.continuous,
            }
        return changed_at
