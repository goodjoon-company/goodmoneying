from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

import pytest

from goodmoneying_shared.backtest_store import (
    BacktestCursorMismatchError,
    BacktestIdempotencyConflictError,
    BacktestInputNotReadyError,
    create_run,
    list_run_equity_points,
    list_run_trades,
)


def test_백테스트_trade_페이지는_첫_페이지_상한을_cursor에_고정한다() -> None:
    repository = _BacktestResultRepository()

    first_page = list_run_trades(repository, backtest_run_id=21, page_size=1, cursor=None)
    assert first_page is not None
    repository.trades.append(_trade(3))
    second_page = list_run_trades(
        repository,
        backtest_run_id=21,
        page_size=1,
        cursor=first_page["nextCursor"],
    )
    assert second_page is not None

    assert [item["tradeSequence"] for item in first_page["items"]] == [1]
    assert [item["tradeSequence"] for item in second_page["items"]] == [2]
    assert repository.trade_parameters[-1] == (21, 2, 1, 2)


def test_백테스트_equity_페이지는_run_문맥이_다른_cursor를_거부한다() -> None:
    repository = _BacktestResultRepository()
    first_page = list_run_equity_points(
        repository,
        backtest_run_id=21,
        page_size=1,
        cursor=None,
    )
    assert first_page is not None

    with pytest.raises(BacktestCursorMismatchError):
        list_run_equity_points(
            repository,
            backtest_run_id=22,
            page_size=1,
            cursor=first_page["nextCursor"],
        )


def test_백테스트_run_생성은_ready_input_hash를_고정해_queued로_저장한다() -> None:
    repository = _BacktestCreateRepository()

    created = create_run(
        repository,
        request_id="backtest-request-1",
        idempotency_key="backtest-key-1",
        actor_id="operator:test",
        requested_at=datetime(2026, 7, 18, 8, tzinfo=UTC),
        reason="P4-7 백테스트 실행 생성",
        strategy_version_id=41,
        dataset_version_id=12,
        engine_version="backtest-core-v1",
        parameters={"entryQuantity": "0.1"},
        seed=42,
        initial_cash=Decimal("1000000"),
        execution={
            "feeRate": Decimal("0.0005"),
            "slippageBps": Decimal("5"),
            "latencySeconds": 60,
            "maxParticipationRate": Decimal("0.25"),
        },
        max_attempts=3,
    )

    assert created["backtestRunId"] == 23
    assert created["status"] == "pending"
    assert created["strategyVersionId"] == 41
    assert created["datasetVersionId"] == 12
    assert created["engineVersion"] == "backtest-core-v1"
    assert created["resultHash"] is None
    assert created["startedAt"] is None
    assert created["finishedAt"] is None
    assert len(created["inputHash"]) == 64
    assert repository.inserted_run is not None
    assert repository.inserted_run["strategy_graph_hash"] == "a" * 64
    assert repository.inserted_run["dataset_content_hash"] == "d" * 64
    assert repository.inserted_run["status"] == "queued"
    assert repository.inserted_run["input_payload"]["kind"] == "backtest-run-input-v1"
    assert repository.inserted_run["input_payload"]["spec"]["initialCash"] == "1000000"
    assert repository.inserted_run["max_attempts"] == 3


def test_백테스트_run_생성은_같은_멱등키의_다른_본문을_거부한다() -> None:
    repository = _BacktestCreateRepository(existing_request_hash="0" * 64)

    with pytest.raises(BacktestIdempotencyConflictError):
        create_run(
            repository,
            request_id="backtest-request-1",
            idempotency_key="backtest-key-1",
            actor_id="operator:test",
            requested_at=datetime(2026, 7, 18, 8, tzinfo=UTC),
            reason="P4-7 백테스트 실행 생성",
            strategy_version_id=41,
            dataset_version_id=12,
            engine_version="backtest-core-v1",
            parameters={"entryQuantity": "0.1"},
            seed=42,
            initial_cash=Decimal("1000000"),
            execution={
                "feeRate": Decimal("0.0005"),
                "slippageBps": Decimal("5"),
                "latencySeconds": 60,
                "maxParticipationRate": Decimal("0.25"),
            },
            max_attempts=3,
        )


def test_백테스트_run_생성은_같은_input_hash를_다른_멱등키로_중복생성하지_않는다() -> None:
    repository = _BacktestCreateRepository(duplicate_input=True)

    with pytest.raises(BacktestIdempotencyConflictError):
        create_run(
            repository,
            request_id="backtest-request-2",
            idempotency_key="backtest-key-2",
            actor_id="operator:test",
            requested_at=datetime(2026, 7, 18, 8, tzinfo=UTC),
            reason="P4-7 백테스트 실행 생성",
            strategy_version_id=41,
            dataset_version_id=12,
            engine_version="backtest-core-v1",
            parameters={"entryQuantity": "0.1"},
            seed=42,
            initial_cash=Decimal("1000000"),
            execution={
                "feeRate": Decimal("0.0005"),
                "slippageBps": Decimal("5"),
                "latencySeconds": 60,
                "maxParticipationRate": Decimal("0.25"),
            },
            max_attempts=3,
        )


def test_백테스트_run_생성은_published_sealed_입력이_아니면_거부한다() -> None:
    repository = _BacktestCreateRepository(input_ready=False)

    with pytest.raises(BacktestInputNotReadyError):
        create_run(
            repository,
            request_id="backtest-request-1",
            idempotency_key="backtest-key-1",
            actor_id="operator:test",
            requested_at=datetime(2026, 7, 18, 8, tzinfo=UTC),
            reason="P4-7 백테스트 실행 생성",
            strategy_version_id=41,
            dataset_version_id=12,
            engine_version="backtest-core-v1",
            parameters={},
            seed=42,
            initial_cash=Decimal("1000000"),
            execution={
                "feeRate": Decimal("0.0005"),
                "slippageBps": Decimal("5"),
                "latencySeconds": 60,
                "maxParticipationRate": Decimal("0.25"),
            },
            max_attempts=3,
        )


class _Result:
    def __init__(
        self,
        *,
        row: dict[str, Any] | None = None,
        rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self._row = row
        self._rows = rows or []

    def fetchone(self) -> dict[str, Any] | None:
        return self._row

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class _BacktestResultConnection:
    def __init__(self, repository: _BacktestResultRepository) -> None:
        self._repository = repository

    def __enter__(self) -> _BacktestResultConnection:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, sql: str, parameters: tuple[object, ...] = ()) -> _Result:
        if "SELECT 1 FROM backtest_runs WHERE id=%s" in sql:
            return _Result(row={"exists": 1} if parameters[0] in {21, 22} else None)
        if "COALESCE(MAX(trade_sequence), 0)" in sql:
            return _Result(
                row={"sequence": max(row["trade_sequence"] for row in self._repository.trades)}
            )
        if "FROM backtest_trades" in sql:
            run_id, ceiling, last_sequence, limit = parameters
            limit_int = cast(int, limit)
            self._repository.trade_parameters.append((run_id, ceiling, last_sequence, limit))
            rows = [
                row
                for row in self._repository.trades
                if row["run_id"] == run_id
                and row["trade_sequence"] <= ceiling
                and row["trade_sequence"] > last_sequence
            ][:limit_int]
            return _Result(rows=rows)
        if "COALESCE(MAX(point_sequence), 0)" in sql:
            return _Result(
                row={"sequence": max(row["point_sequence"] for row in self._repository.equity)}
            )
        if "FROM backtest_equity_points" in sql:
            run_id, ceiling, last_sequence, limit = parameters
            limit_int = cast(int, limit)
            rows = [
                row
                for row in self._repository.equity
                if row["run_id"] == run_id
                and row["point_sequence"] <= ceiling
                and row["point_sequence"] > last_sequence
            ][:limit_int]
            return _Result(rows=rows)
        raise AssertionError(sql)


class _BacktestResultRepository:
    def __init__(self) -> None:
        self.trades = [_trade(1), _trade(2)]
        self.equity = [_equity(1), _equity(2)]
        self.trade_parameters: list[tuple[object, ...]] = []

    def _connect(self) -> _BacktestResultConnection:
        return _BacktestResultConnection(self)


class _BacktestCreateConnection:
    def __init__(self, repository: _BacktestCreateRepository) -> None:
        self._repository = repository

    def __enter__(self) -> _BacktestCreateConnection:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, sql: str, parameters: tuple[object, ...] = ()) -> _Result:
        if "pg_advisory_xact_lock" in sql:
            return _Result(row={"locked": 1})
        if "FROM backtest_runs WHERE idempotency_key=%s" in sql:
            if self._repository.existing_request_hash is None:
                return _Result()
            return _Result(
                row={"id": 23, "request_hash": self._repository.existing_request_hash}
            )
        if "FROM strategy_versions strategy" in sql and "dataset_versions version" in sql:
            if not self._repository.input_ready:
                return _Result()
            return _Result(
                row={
                    "strategy_version_id": 41,
                    "strategy_graph_hash": "a" * 64,
                    "dataset_version_id": 12,
                    "dataset_content_hash": "d" * 64,
                    "dataset_as_of": datetime(2026, 7, 18, 7, tzinfo=UTC),
                    "dataset_from": datetime(2026, 7, 18, 6, tzinfo=UTC),
                    "dataset_to": datetime(2026, 7, 18, 7, tzinfo=UTC),
                    "fill_policy": "none",
                    "missing_policy": "fail",
                }
            )
        if "FROM backtest_runs WHERE input_hash=%s" in sql:
            if not self._repository.duplicate_input:
                return _Result()
            return _Result(row={"id": 99})
        if "FROM dataset_version_candles" in sql:
            return _Result(rows=[])
        if "INSERT INTO backtest_runs" in sql and "RETURNING" in sql:
            (
                strategy_version_id,
                strategy_graph_hash,
                dataset_version_id,
                dataset_content_hash,
                engine_version,
                input_hash,
                input_payload,
                parameter_hash,
                seed,
                assumptions,
                idempotency_key,
                request_id,
                actor_id,
                requested_at,
                reason,
                request_hash,
                max_attempts,
            ) = parameters
            self._repository.inserted_run = {
                "id": 23,
                "strategy_version_id": strategy_version_id,
                "strategy_graph_hash": strategy_graph_hash,
                "dataset_version_id": dataset_version_id,
                "dataset_content_hash": dataset_content_hash,
                "engine_version": engine_version,
                "status": "queued",
                "input_hash": input_hash,
                "input_payload": getattr(input_payload, "obj", input_payload),
                "result_hash": None,
                "parameter_hash": parameter_hash,
                "seed": seed,
                "assumptions": getattr(assumptions, "obj", assumptions),
                "idempotency_key": idempotency_key,
                "request_id": request_id,
                "actor_id": actor_id,
                "requested_at": requested_at,
                "reason": reason,
                "request_hash": request_hash,
                "started_at": None,
                "finished_at": None,
                "max_attempts": max_attempts,
            }
            return _Result(row=self._repository.inserted_run)
        raise AssertionError(sql)


class _BacktestCreateRepository:
    def __init__(
        self,
        *,
        existing_request_hash: str | None = None,
        input_ready: bool = True,
        duplicate_input: bool = False,
    ) -> None:
        self.existing_request_hash = existing_request_hash
        self.input_ready = input_ready
        self.duplicate_input = duplicate_input
        self.inserted_run: dict[str, Any] | None = None

    def _connect(self) -> _BacktestCreateConnection:
        return _BacktestCreateConnection(self)


def _trade(sequence: int) -> dict[str, Any]:
    at = datetime(2026, 7, 18, tzinfo=UTC)
    return {
        "run_id": 21,
        "trade_sequence": sequence,
        "side": "buy",
        "requested_quantity": Decimal("3"),
        "filled_quantity": Decimal("1"),
        "remaining_quantity": Decimal("2"),
        "fill_price": Decimal("100"),
        "fee_paid": Decimal("0.1"),
        "status": "partially_filled",
        "occurred_at": at,
        "knowledge_at": at,
    }


def _equity(sequence: int) -> dict[str, Any]:
    at = datetime(2026, 7, 18, tzinfo=UTC)
    return {
        "run_id": 21,
        "point_sequence": sequence,
        "occurred_at": at,
        "knowledge_at": at,
        "cash": Decimal("900"),
        "base_position": Decimal("1"),
        "equity": Decimal("1000"),
    }
