from __future__ import annotations

import argparse
import threading
from collections.abc import Callable
from decimal import Decimal
from functools import wraps
from typing import Any, cast

import uvicorn
from fastapi import FastAPI

from goodmoneying_api.main import create_app
from goodmoneying_shared.models import SourceCandle
from goodmoneying_shared.repository import OperationsRepository
from goodmoneying_shared.sqlite_repository import SQLiteOperationsRepository
from goodmoneying_shared.time import now_kst
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
    target = repository.list_active_targets()[0]
    repository.record_collection_worker_heartbeat("realtime_collection", "running")
    repository.record_collection_worker_heartbeat("backfill_collection", "running")
    repository.record_collection_worker_heartbeat("candle_aggregation", "running")
    repository.record_incremental_collection(
        [],
        [],
        [
            SourceCandle(
                instrument_id=target.id,
                candle_unit="1m",
                candle_start_at=now_kst(),
                open_price=Decimal("1"),
                high_price=Decimal("1"),
                low_price=Decimal("1"),
                close_price=Decimal("1"),
                trade_volume=Decimal("1"),
                trade_amount=Decimal("1"),
                collected_at=now_kst(),
            )
        ],
    )
    repository.schedule_candle_aggregation()
    serialized_repository = cast(
        OperationsRepository,
        _SerializedOperationsRepository(repository),
    )
    _start_aggregation_heartbeat(serialized_repository)
    return create_app(serialized_repository)


def main() -> None:
    parser = argparse.ArgumentParser(description="격리된 goodmoneying E2E API 서버")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18000)
    args = parser.parse_args()
    uvicorn.run(create_seeded_e2e_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
