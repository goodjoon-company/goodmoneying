from __future__ import annotations

import argparse
import threading
from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from functools import wraps
from typing import Any, cast
from uuid import uuid4

import uvicorn
from fastapi import FastAPI

from goodmoneying_api.main import create_app
from goodmoneying_shared.models import SourceCandle
from goodmoneying_shared.repository import OperationsRepository
from goodmoneying_shared.sqlite_repository import SQLiteOperationsRepository
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
            repository.record_collection_worker_heartbeat(
                "candle_aggregation", "running"
            )
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
    app = create_app(serialized_repository)
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
                "candle_date_time_utc": f"2026-07-16T00:{minute_base - index:02d}:00",
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
