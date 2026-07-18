from __future__ import annotations

import argparse
import threading
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import wraps
from typing import Any, cast
from uuid import uuid4

import uvicorn
from fastapi import FastAPI

from goodmoneying_api.main import create_app
from goodmoneying_shared.data_foundation import (
    DEFAULT_KRW_START_AT,
    CoverageState,
    DataFoundationOverview,
    MarketCollectionPolicySettings,
    MarketCollectionStatus,
)
from goodmoneying_shared.models import SourceCandle
from goodmoneying_shared.repository import OperationsRepository
from goodmoneying_shared.sqlite_repository import SQLiteOperationsRepository
from goodmoneying_shared.strategy_graph import validate_strategy_graph
from goodmoneying_shared.time import now_kst
from goodmoneying_upbit_gateway.catalog import load_catalog, rest_endpoint_by_id
from goodmoneying_worker.collector import seed_repository
from goodmoneying_worker.upbit_client import FixtureUpbitClient


class _SerializedOperationsRepository:
    def __init__(self, repository: OperationsRepository) -> None:
        self._repository = repository
        self._lock = threading.RLock()

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._repository, name)
        if not callable(attribute):
            return attribute

        callable_attribute = cast(Callable[..., Any], attribute)

        @wraps(callable_attribute)
        def synchronized(*args: Any, **kwargs: Any) -> Any:
            with self._lock:
                return callable_attribute(*args, **kwargs)

        return synchronized


class _SeededDataFoundationRepository:
    def __init__(self) -> None:
        self._markets = [
            MarketCollectionStatus(
                instrument_id=41,
                market_code="KRW-BTC",
                korean_name="비트코인",
                english_name="Bitcoin",
                quote_currency="KRW",
                trading_status="active",
                market_warning="NONE",
                target_status="active",
                active_data_type_count=4,
                total_data_type_count=4,
                coverage_counts={
                    "available": 24,
                    "no_trade": 3,
                    "missing": 0,
                    "unavailable": 2,
                    "unverified": 1,
                },
                collection_policy=MarketCollectionPolicySettings(
                    start_at=DEFAULT_KRW_START_AT,
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
                instrument_id=42,
                market_code="KRW-ETH",
                korean_name="이더리움",
                english_name="Ethereum",
                quote_currency="KRW",
                trading_status="active",
                market_warning="CAUTION",
                target_status="paused",
                active_data_type_count=0,
                total_data_type_count=4,
                coverage_counts={
                    "available": 18,
                    "no_trade": 0,
                    "missing": 1,
                    "unavailable": 3,
                    "unverified": 2,
                },
                collection_policy=MarketCollectionPolicySettings(
                    start_at=DEFAULT_KRW_START_AT,
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
        ]

    def overview(self) -> DataFoundationOverview:
        coverage_counts: dict[CoverageState, int] = {
            "available": 0,
            "no_trade": 0,
            "missing": 0,
            "unavailable": 0,
            "unverified": 0,
        }
        for market in self._markets:
            for status in coverage_counts:
                coverage_counts[status] += market.coverage_counts[status]
        return DataFoundationOverview(
            market_count=len(self._markets),
            krw_market_count=len(self._markets),
            active_target_count=sum(market.active_data_type_count for market in self._markets),
            pending_backfill_job_count=1,
            desired_subscription_count=3,
            policy_start_at=DEFAULT_KRW_START_AT,
            coverage_counts=coverage_counts,
            markets=list(self._markets),
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
        del actor, reason, request_id, idempotency_key, requested_at
        if changed_at.tzinfo is None or changed_at.utcoffset() != UTC.utcoffset(changed_at):
            raise ValueError("changed_at은 UTC여야 한다.")
        for index, market in enumerate(self._markets):
            if market.market_code != market_code:
                continue
            if state not in {"active", "paused", "excluded"}:
                raise ValueError("지원하지 않는 상태다.")
            self._markets[index] = replace(
                market,
                target_status=state,  # type: ignore[arg-type]
                active_data_type_count=market.total_data_type_count if state == "active" else 0,
                collection_policy=policy or market.collection_policy,
            )
            return changed_at
        raise ValueError("변경할 시장을 찾을 수 없다.")


class _SeededDatasetVersionRepository:
    def __init__(self) -> None:
        self._builds: list[dict[str, object]] = [
            {
                "buildId": 7,
                "requestId": "dataset-seeded-retry",
                "idempotencyKey": "dataset-seeded-retry",
                "actorId": "operator:e2e",
                "requestedAt": "2026-07-17T06:00:00Z",
                "frozenAt": "2026-07-17T06:00:01Z",
                "status": "retry_wait",
                "attemptCount": 2,
                "maxAttempts": 3,
                "nextRetryAt": "2026-07-17T06:05:00Z",
                "deadLetterReason": None,
                "datasetVersionId": None,
                "errorCode": None,
                "errorMessage": None,
            }
        ]
        self._series = {
            "instrumentId": 41,
            "dataKind": "candle",
            "unit": "1m",
            "definitionSetHash": None,
            "calculationVersion": "source-candle-v1",
        }
        self._versions: list[dict[str, object]] = [
            {
                "datasetVersionId": 12,
                "schemaVersion": "dataset-v1",
                "asOf": "2026-07-17T05:00:00Z",
                "from": "2026-07-17T00:00:00Z",
                "to": "2026-07-17T02:00:00Z",
                "contentHash": "b" * 64,
                "availabilityPolicy": "point_in_time_v1",
                "fillPolicy": "none",
                "missingPolicy": "fail",
                "createdAt": "2026-07-17T06:00:03Z",
                "series": [{**self._series, "seriesId": 202}],
            },
            {
                "datasetVersionId": 11,
                "schemaVersion": "dataset-v1",
                "asOf": "2026-07-17T05:00:00Z",
                "from": "2026-07-17T00:00:00Z",
                "to": "2026-07-17T02:00:00Z",
                "contentHash": "a" * 64,
                "availabilityPolicy": "point_in_time_v1",
                "fillPolicy": "none",
                "missingPolicy": "fail",
                "createdAt": "2026-07-17T06:00:02Z",
                "series": [{**self._series, "seriesId": 101}],
            },
        ]

    def create_build(
        self,
        *,
        request_id: str,
        idempotency_key: str,
        actor_id: str,
        requested_at: datetime,
        reason: str,
        selection: Mapping[str, object],
        policies: Mapping[str, object],
    ) -> dict[str, object]:
        del reason, selection, policies
        build = {
            "buildId": max(cast(int, item["buildId"]) for item in self._builds) + 1,
            "requestId": request_id,
            "idempotencyKey": idempotency_key,
            "actorId": actor_id,
            "requestedAt": requested_at.isoformat().replace("+00:00", "Z"),
            "frozenAt": now_kst().astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "status": "pending",
            "attemptCount": 0,
            "maxAttempts": 3,
            "nextRetryAt": None,
            "deadLetterReason": None,
            "datasetVersionId": None,
            "errorCode": None,
            "errorMessage": None,
        }
        self._builds.insert(0, build)
        return build

    def get_build(self, build_id: int) -> dict[str, object] | None:
        return next((item for item in self._builds if item["buildId"] == build_id), None)

    def list_builds(self, *, page_size: int, cursor: str | None) -> dict[str, object]:
        del cursor
        return {"items": self._builds[:page_size], "nextCursor": None}

    def get_version(self, dataset_version_id: int) -> dict[str, object] | None:
        return next(
            (item for item in self._versions if item["datasetVersionId"] == dataset_version_id),
            None,
        )

    def list_versions(self, *, page_size: int, cursor: str | None) -> dict[str, object]:
        del cursor
        return {"items": self._versions[:page_size], "nextCursor": None}

    def get_coverage(self, dataset_version_id: int) -> dict[str, object] | None:
        if self.get_version(dataset_version_id) is None:
            return None
        base_at = datetime(2026, 7, 17, tzinfo=UTC)
        return {
            "datasetVersionId": dataset_version_id,
            "snapshotHash": "c" * 64,
            "requestedBucketCount": 80,
            "eligibleBucketCount": 40,
            "usableRatio": "0.5000",
            "counts": {
                "available": 40,
                "no_trade": 0,
                "missing": 0,
                "unavailable": 0,
                "unverified": 40,
            },
            "items": [
                {
                    "seriesId": 202,
                    "rangeStartAt": (base_at + timedelta(minutes=index))
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "rangeEndAt": (base_at + timedelta(minutes=index + 1))
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "knowledgeAt": "2026-07-17T00:00:02Z",
                    "status": "available" if index % 2 == 0 else "unverified",
                    "bucketCount": 1,
                }
                for index in range(80)
            ],
        }

    def get_series(
        self,
        *,
        dataset_version_id: int,
        series_id: int,
        from_at: datetime,
        to_at: datetime,
        page_size: int,
        cursor: str | None,
    ) -> dict[str, object] | None:
        del from_at, to_at, page_size, cursor
        if self.get_version(dataset_version_id) is None:
            return None
        return {
            "datasetVersionId": dataset_version_id,
            "seriesId": series_id,
            "dataKind": "candle",
            "unit": "1m",
            "items": [
                {
                    "occurredAt": "2026-07-17T00:00:00Z",
                    "knowledgeAt": "2026-07-17T00:00:02Z",
                    "quality": "available",
                    "contentHash": "d" * 64,
                    "values": {"open": "100", "close": "101"},
                }
            ],
            "nextCursor": None,
        }


class _SeededStrategyRepository:
    def __init__(self) -> None:
        self._strategies: list[dict[str, object]] = []
        self._versions: list[dict[str, object]] = []

    def validate_graph(self, *, graph: Mapping[str, object]) -> dict[str, object]:
        return validate_strategy_graph(graph).to_api()

    def create_strategy(
        self,
        *,
        request_id: str,
        idempotency_key: str,
        actor_id: str,
        requested_at: datetime,
        reason: str,
        owner_id: str,
        name: str,
    ) -> dict[str, object]:
        del request_id, idempotency_key, actor_id, requested_at, reason
        strategy = {
            "strategyId": len(self._strategies) + 1,
            "ownerId": owner_id,
            "name": name,
            "createdAt": now_kst().astimezone(UTC).isoformat().replace("+00:00", "Z"),
        }
        self._strategies.append(strategy)
        return strategy

    def publish_version(
        self,
        *,
        strategy_id: int,
        request_id: str,
        idempotency_key: str,
        actor_id: str,
        requested_at: datetime,
        reason: str,
        graph: Mapping[str, object],
    ) -> dict[str, object]:
        del request_id, idempotency_key, reason
        validation = self.validate_graph(graph=graph)
        if not validation["valid"]:
            raise ValueError("검증을 통과한 전략 graph만 게시할 수 있다.")
        now = now_kst().astimezone(UTC).isoformat().replace("+00:00", "Z")
        version = {
            "strategyVersionId": len(self._versions) + 1,
            "strategyId": strategy_id,
            "version": 1 + sum(item["strategyId"] == strategy_id for item in self._versions),
            "schemaVersion": "strategy-graph-v1",
            "status": "published",
            "graphHash": validation["graphHash"],
            "validation": validation,
            "graph": dict(graph),
            "actorId": actor_id,
            "requestedAt": requested_at.isoformat().replace("+00:00", "Z"),
            "createdAt": now,
            "publishedAt": now,
        }
        self._versions.append(version)
        return version

    def list_versions(
        self, *, strategy_id: int, page_size: int, cursor: str | None
    ) -> dict[str, object]:
        del cursor
        return {
            "items": [item for item in self._versions if item["strategyId"] == strategy_id][
                :page_size
            ],
            "nextCursor": None,
        }

    def get_version(self, strategy_version_id: int) -> dict[str, object] | None:
        return next(
            (item for item in self._versions if item["strategyVersionId"] == strategy_version_id),
            None,
        )


class _SeededBacktestRepository:
    def get_run_summary(self, backtest_run_id: int) -> dict[str, object] | None:
        runs = self.list_runs(page_size=100, cursor=None)["items"]
        return next(
            (
                item
                for item in cast(list[dict[str, object]], runs)
                if item["backtestRunId"] == backtest_run_id
            ),
            None,
        )

    def create_run(self, **_arguments: object) -> dict[str, object]:
        return {
            "backtestRunId": 23,
            "strategyVersionId": 41,
            "datasetVersionId": 12,
            "engineVersion": "backtest-core-v1",
            "status": "pending",
            "inputHash": "0" * 64,
            "resultHash": None,
            "requestedAt": "2026-07-18T08:00:00Z",
            "startedAt": None,
            "finishedAt": None,
        }

    def list_runs(self, *, page_size: int, cursor: str | None) -> dict[str, object]:
        del cursor
        return {
            "items": [
                {
                    "backtestRunId": 21,
                    "strategyVersionId": 41,
                    "datasetVersionId": 12,
                    "engineVersion": "backtest-core-v1",
                    "status": "succeeded",
                    "inputHash": "e" * 64,
                    "resultHash": "f" * 64,
                    "requestedAt": "2026-07-18T00:00:00Z",
                    "startedAt": "2026-07-18T00:00:00Z",
                    "finishedAt": "2026-07-18T00:00:00Z",
                }
            ][:page_size],
            "nextCursor": None,
        }

    def get_run(self, backtest_run_id: int) -> dict[str, object] | None:
        if backtest_run_id != 21:
            return None
        return {
            "backtestRunId": 21,
            "strategyVersionId": 41,
            "datasetVersionId": 12,
            "status": "succeeded",
            "inputHash": "e" * 64,
            "resultHash": "f" * 64,
            "metrics": [
                {
                    "metricName": "finalEquity",
                    "scopeKey": "run",
                    "metricValue": Decimal("1009.579790"),
                    "metricPayload": {},
                }
            ],
            "trades": [
                {
                    "tradeSequence": 1,
                    "side": "buy",
                    "requestedQuantity": Decimal("3"),
                    "filledQuantity": Decimal("1.00"),
                    "remainingQuantity": Decimal("2.00"),
                    "fillPrice": Decimal("100.100"),
                    "feePaid": Decimal("0.100100"),
                    "status": "partially_filled",
                    "occurredAt": "2026-07-18T00:00:00Z",
                    "knowledgeAt": "2026-07-18T00:00:00Z",
                }
            ],
            "artifacts": [
                {
                    "artifactType": "walk_forward_summary",
                    "contentHash": "c" * 64,
                    "mediaType": "application/json",
                    "storageUri": "artifact://p4-3/walk-forward",
                    "metadata": {"folds": 3},
                }
            ],
        }

    def list_run_trades(self, **arguments: object) -> dict[str, object] | None:
        backtest_run_id = int(cast(int | str, arguments["backtest_run_id"]))
        run = self.get_run(backtest_run_id)
        if run is None:
            return None
        return {
            "backtestRunId": backtest_run_id,
            "items": run["trades"],
            "nextCursor": None,
        }

    def list_run_equity_points(self, **arguments: object) -> dict[str, object] | None:
        backtest_run_id = int(cast(int | str, arguments["backtest_run_id"]))
        if backtest_run_id != 21:
            return None
        return {
            "backtestRunId": backtest_run_id,
            "items": [
                {
                    "pointSequence": 1,
                    "occurredAt": "2026-07-18T00:00:00Z",
                    "knowledgeAt": "2026-07-18T00:00:00Z",
                    "cash": Decimal("899.799900"),
                    "basePosition": Decimal("1.00"),
                    "equity": Decimal("1009.579790"),
                }
            ],
            "nextCursor": None,
        }

def _analysis_history_candles(
    first_instrument_id: int, second_instrument_id: int
) -> list[SourceCandle]:
    day_start = now_kst().replace(hour=0, minute=0, second=0, microsecond=0)
    candles: list[SourceCandle] = []
    histories = (
        (first_instrument_id, Decimal("1000000"), (1000, 300, 30)),
        (second_instrument_id, Decimal("2000000"), (900, 20)),
    )
    for instrument_id, price_base, day_offsets in histories:
        for index, day_offset in enumerate(day_offsets, start=1):
            started_at = day_start - timedelta(days=day_offset)
            open_price = price_base + Decimal(index)
            candles.append(
                SourceCandle(
                    instrument_id=instrument_id,
                    candle_unit="1d",
                    candle_start_at=started_at,
                    open_price=open_price,
                    high_price=open_price + Decimal("10"),
                    low_price=open_price - Decimal("10"),
                    close_price=open_price + Decimal("5"),
                    trade_volume=Decimal(index * 10),
                    trade_amount=(open_price + Decimal("5")) * Decimal(index * 10),
                    collected_at=started_at,
                )
            )
    return candles


def _start_aggregation_heartbeat(repository: OperationsRepository) -> None:
    interval = threading.Event()

    def maintain_heartbeat() -> None:
        while True:
            repository.record_collection_worker_heartbeat("candle_aggregation", "running")
            interval.wait(5)

    threading.Thread(
        target=maintain_heartbeat,
        name="seeded-e2e-aggregation-heartbeat",
        daemon=True,
    ).start()


def create_seeded_e2e_app() -> FastAPI:
    repository = SQLiteOperationsRepository()
    seed_repository(repository, FixtureUpbitClient())
    first_target, second_target = repository.list_active_targets()[:2]
    repository.record_collection_worker_heartbeat("realtime_collection", "running")
    repository.record_collection_worker_heartbeat("backfill_collection", "running")
    repository.record_collection_worker_heartbeat("candle_aggregation", "running")
    repository.record_incremental_collection(
        [],
        [],
        [
            SourceCandle(
                instrument_id=first_target.id,
                candle_unit="1m",
                candle_start_at=now_kst(),
                open_price=Decimal("1"),
                high_price=Decimal("1"),
                low_price=Decimal("1"),
                close_price=Decimal("1"),
                trade_volume=Decimal("1"),
                trade_amount=Decimal("1"),
                collected_at=now_kst(),
            ),
            *_analysis_history_candles(first_target.id, second_target.id),
        ],
    )
    repository.schedule_candle_aggregation()
    serialized_repository = cast(
        OperationsRepository,
        _SerializedOperationsRepository(repository),
    )
    _start_aggregation_heartbeat(serialized_repository)
    app = create_app(
        serialized_repository,
        data_foundation_repository=_SeededDataFoundationRepository(),
        dataset_version_repository=_SeededDatasetVersionRepository(),
        strategy_repository=_SeededStrategyRepository(),
        backtest_repository=_SeededBacktestRepository(),
    )
    catalog = load_catalog()

    @app.get("/v1/catalog")
    def get_fake_upbit_catalog() -> dict[str, Any]:
        return catalog

    @app.post("/v1/requests")
    def execute_fake_upbit_request(payload: dict[str, Any]) -> dict[str, Any]:
        endpoint_id = str(payload.get("endpoint_id", ""))
        parameters = cast(dict[str, Any], payload.get("parameters", {}))
        endpoint = rest_endpoint_by_id(catalog, endpoint_id)
        if endpoint is None:
            return {
                "detail": {
                    "code": "UNKNOWN_ENDPOINT",
                    "message": "가짜 카탈로그에 없는 기능입니다.",
                }
            }
        body = _fake_upbit_body(endpoint_id, parameters)
        return {
            "trace_id": str(uuid4()),
            "endpoint_id": endpoint_id,
            "request": {
                "method": endpoint["method"],
                "path": endpoint["path"],
                "parameters": parameters,
            },
            "response": {"status_code": 200, "body": body},
            "rate_limit": {
                "group": endpoint["rate_limit_group"],
                "remaining_sec": 9,
                "retry_after": None,
            },
            "duration_ms": 3.2,
            "received_at": now_kst().isoformat(),
        }

    return app


def _fake_upbit_body(endpoint_id: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
    if endpoint_id == "rest.list-trading-pairs":
        return [
            {"market": "KRW-BTC", "korean_name": "비트코인", "english_name": "Bitcoin"},
            {"market": "KRW-ETH", "korean_name": "이더리움", "english_name": "Ethereum"},
        ]
    if "candles" in endpoint_id:
        past = "to" in parameters
        minute_base = 19 if not past else 9
        return [
            {
                "market": str(parameters.get("market", "KRW-BTC")),
                "candle_date_time_utc": f"2026-07-15T00:{minute_base - index:02d}:00",
                "opening_price": 100 + index,
                "high_price": 110 + index,
                "low_price": 90 + index,
                "trade_price": 105 + index,
                "candle_acc_trade_volume": 10 + index,
                "candle_acc_trade_price": 1000 + index,
            }
            for index in range(10)
        ]
    if endpoint_id == "rest.list-pair-trades":
        return [
            {
                "market": "KRW-BTC",
                "trade_price": 150_000_000,
                "trade_volume": 0.1,
                "ask_bid": "BID",
                "timestamp": 1_768_000_000_000,
            }
        ]
    if "tickers" in endpoint_id:
        return [
            {
                "market": "KRW-BTC",
                "trade_price": 150_000_000,
                "acc_trade_price_24h": 90_000_000_000,
            }
        ]
    if endpoint_id == "rest.list-orderbooks":
        return [
            {
                "market": "KRW-BTC",
                "orderbook_units": [
                    {
                        "ask_price": 150_001_000,
                        "ask_size": 0.2,
                        "bid_price": 150_000_000,
                        "bid_size": 0.3,
                    }
                ],
            }
        ]
    if endpoint_id == "rest.list-orderbook-instruments":
        return [{"market": "KRW-BTC", "supported_levels": [0, 10_000], "tick_size": 1000}]
    return [{"market": "KRW-BTC", "supported_levels": [0, 10_000]}]


def main() -> None:
    parser = argparse.ArgumentParser(description="격리된 goodmoneying E2E API 서버")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18000)
    args = parser.parse_args()
    uvicorn.run(create_seeded_e2e_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
