from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from goodmoneying_shared.backtest_engine import BacktestResult
from goodmoneying_shared.backtest_store import (
    BacktestLeaseLostError,
    claim_next_run,
    complete_claimed_run,
    fail_claimed_run,
)


def test_백테스트_claim은_skip_locked와_generation_fencing으로_run을_임대한다() -> None:
    repository = _BacktestLeaseRepository(_queued_row())

    claimed = claim_next_run(repository, worker_id="worker-a", lease_seconds=90)

    assert claimed is not None
    assert claimed["backtestRunId"] == 101
    assert claimed["status"] == "running"
    assert claimed["leaseOwner"] == "worker-a"
    assert claimed["leaseGeneration"] == 3
    assert claimed["attemptCount"] == 2
    executed_sql = "\n".join(repository.sql)
    assert "FOR UPDATE SKIP LOCKED" in executed_sql
    assert "lease_generation=%s" in executed_sql
    assert "attempt_count=attempt_count+1" in executed_sql


def test_백테스트_claim은_임대_만료와_재시도_대기_만료_run만_선택한다() -> None:
    repository = _BacktestLeaseRepository(_queued_row(status="retry_wait"))

    claim_next_run(repository, worker_id="worker-a")

    executed_sql = "\n".join(repository.sql)
    assert "status='retry_wait' AND next_retry_at <= clock_timestamp()" in executed_sql
    assert "status='running' AND lease_expires_at <= clock_timestamp()" in executed_sql
    assert "attempt_count < max_attempts" in executed_sql


def test_백테스트_완료는_현재_generation에_결과와_artifact를_트랜잭션으로_저장한다() -> None:
    result = _result()
    repository = _BacktestLeaseRepository(
        _queued_row(status="running", lease_owner="worker-a", lease_generation=7)
        | {"input_hash": result.input_hash}
    )

    saved = complete_claimed_run(
        repository,
        backtest_run_id=101,
        worker_id="worker-a",
        lease_generation=7,
        result=result,
        artifacts=(
            {
                "artifactType": "worker_summary",
                "contentHash": "a" * 64,
                "metadata": {"rows": 1},
            },
        ),
        completed_at=datetime(2026, 7, 18, 12, tzinfo=UTC),
    )

    assert saved["backtestRunId"] == 101
    assert repository.row["status"] == "succeeded"
    assert repository.row["result_hash"] == result.result_hash
    assert repository.inserted["artifacts"] == ["worker_summary"]
    executed_sql = "\n".join(repository.sql)
    assert "lease_owner=%s" in executed_sql
    assert "lease_generation=%s" in executed_sql
    assert "lease_expires_at > clock_timestamp()" in executed_sql


def test_백테스트_완료는_늦은_worker의_generation을_거부한다() -> None:
    repository = _BacktestLeaseRepository(
        _queued_row(status="running", lease_owner="worker-b", lease_generation=8)
    )

    with pytest.raises(BacktestLeaseLostError):
        complete_claimed_run(
            repository,
            backtest_run_id=101,
            worker_id="worker-a",
            lease_generation=7,
            result=_result(),
        )

    assert repository.inserted["artifacts"] == []


def test_백테스트_실패는_시도_예산이_남으면_retry_wait로_전이한다() -> None:
    repository = _BacktestLeaseRepository(
        _queued_row(
            status="running",
            lease_owner="worker-a",
            lease_generation=7,
            attempt_count=2,
            max_attempts=3,
        )
    )

    failed = fail_claimed_run(
        repository,
        backtest_run_id=101,
        worker_id="worker-a",
        lease_generation=7,
        error_code="ArithmeticError",
        message="계산 실패",
    )

    assert failed["status"] == "retry_wait"
    assert repository.row["lease_owner"] is None
    assert repository.row["last_error_code"] == "ArithmeticError"
    assert repository.row["finished_at"] is None


def test_백테스트_실패는_시도_예산을_소진하면_dead_letter로_전이한다() -> None:
    repository = _BacktestLeaseRepository(
        _queued_row(
            status="running",
            lease_owner="worker-a",
            lease_generation=7,
            attempt_count=3,
            max_attempts=3,
        )
    )

    failed = fail_claimed_run(
        repository,
        backtest_run_id=101,
        worker_id="worker-a",
        lease_generation=7,
        error_code="ArithmeticError",
        message="계산 실패",
    )

    assert failed["status"] == "dead_letter"
    assert repository.row["dead_letter_reason"] == "ArithmeticError"
    assert repository.row["finished_at"] is not None


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


class _BacktestLeaseConnection:
    def __init__(self, repository: _BacktestLeaseRepository) -> None:
        self._repository = repository

    def __enter__(self) -> _BacktestLeaseConnection:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, sql: str, parameters: tuple[object, ...] = ()) -> _Result:
        self._repository.sql.append(sql)
        row = self._repository.row
        if "UPDATE backtest_runs SET status='dead_letter'" in sql:
            return _Result()
        if "ORDER BY requested_at, id FOR UPDATE SKIP LOCKED LIMIT 1" in sql:
            if row["status"] in {"queued", "retry_wait", "running"}:
                return _Result(row=dict(row))
            return _Result()
        if "UPDATE backtest_runs SET status='running'" in sql:
            worker_id, lease_seconds, generation, run_id, previous_generation = parameters
            assert lease_seconds in {90, 120}
            if row["id"] == run_id and row["lease_generation"] == previous_generation:
                row.update(
                    status="running",
                    lease_owner=worker_id,
                    lease_generation=generation,
                    attempt_count=int(row["attempt_count"]) + 1,
                    next_retry_at=None,
                    dead_letter_reason=None,
                    started_at=row["started_at"] or datetime(2026, 7, 18, 12, tzinfo=UTC),
                    finished_at=None,
                )
                return _Result(row=dict(row))
            return _Result()
        if "SELECT * FROM backtest_runs" in sql and "FOR UPDATE" in sql:
            run_id, worker_id, generation = parameters
            if (
                row["id"] == run_id
                and row["status"] == "running"
                and row["lease_owner"] == worker_id
                and row["lease_generation"] == generation
            ):
                return _Result(row=dict(row))
            return _Result()
        if "INSERT INTO backtest_trades" in sql:
            self._repository.inserted["trades"].append(parameters)
            return _Result()
        if "INSERT INTO backtest_equity_points" in sql:
            self._repository.inserted["equity"].append(parameters)
            return _Result()
        if "INSERT INTO backtest_metrics" in sql:
            self._repository.inserted["metrics"].append(parameters[1])
            return _Result()
        if "INSERT INTO backtest_artifacts" in sql:
            self._repository.inserted["artifacts"].append(parameters[1])
            return _Result()
        if "UPDATE backtest_runs SET status=%s" in sql:
            (
                status,
                result_hash,
                assumptions,
                completed_at,
                run_id,
                worker_id,
                generation,
            ) = parameters
            if (
                row["id"] == run_id
                and row["status"] == "running"
                and row["lease_owner"] == worker_id
                and row["lease_generation"] == generation
            ):
                row.update(
                    status=status,
                    result_hash=result_hash,
                    assumptions=assumptions,
                    lease_owner=None,
                    lease_expires_at=None,
                    finished_at=completed_at,
                )
                return _Result(row=dict(row))
            return _Result()
        if "UPDATE backtest_runs SET" in sql and "CASE WHEN attempt_count >= max_attempts" in sql:
            (
                error_code,
                message,
                dead_letter_reason,
                backtest_run_id,
                worker_id,
                generation,
            ) = parameters
            assert dead_letter_reason == error_code
            if (
                row["id"] == backtest_run_id
                and row["status"] == "running"
                and row["lease_owner"] == worker_id
                and row["lease_generation"] == generation
            ):
                exhausted = int(row["attempt_count"]) >= int(row["max_attempts"])
                row.update(
                    status="dead_letter" if exhausted else "retry_wait",
                    lease_owner=None,
                    lease_expires_at=None,
                    next_retry_at=None
                    if exhausted
                    else datetime(2026, 7, 18, 12, 1, tzinfo=UTC),
                    last_error_code=error_code,
                    last_error_message=message,
                    dead_letter_reason=error_code if exhausted else None,
                    finished_at=datetime(2026, 7, 18, 12, tzinfo=UTC)
                    if exhausted
                    else None,
                )
                return _Result(row=dict(row))
            return _Result()
        if "SELECT * FROM backtest_runs WHERE id=%s" in sql:
            return _Result(row=dict(row))
        if "FROM backtest_trades" in sql:
            return _Result(rows=[])
        if "FROM backtest_metrics" in sql:
            return _Result(rows=[])
        if "FROM backtest_artifacts" in sql:
            return _Result(rows=[])
        raise AssertionError(sql)


class _BacktestLeaseRepository:
    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row
        self.sql: list[str] = []
        self.inserted: dict[str, list[Any]] = {
            "trades": [],
            "equity": [],
            "metrics": [],
            "artifacts": [],
        }

    def _connect(self) -> _BacktestLeaseConnection:
        return _BacktestLeaseConnection(self)


def _queued_row(
    *,
    status: str = "queued",
    lease_owner: str | None = None,
    lease_generation: int = 2,
    attempt_count: int = 1,
    max_attempts: int = 3,
) -> dict[str, Any]:
    at = datetime(2026, 7, 18, 12, tzinfo=UTC)
    return {
        "id": 101,
        "strategy_version_id": 41,
        "strategy_graph_hash": "a" * 64,
        "dataset_version_id": 12,
        "dataset_content_hash": "d" * 64,
        "engine_version": "backtest-core-v1",
        "status": status,
        "input_hash": "e" * 64,
        "result_hash": None,
        "parameter_hash": "b" * 64,
        "seed": 42,
        "assumptions": [],
        "idempotency_key": "backtest-key",
        "request_id": "backtest-request",
        "actor_id": "operator:test",
        "requested_at": at,
        "reason": "P4-5 테스트",
        "request_hash": "c" * 64,
        "started_at": at if status == "running" else None,
        "finished_at": None,
        "created_at": at,
        "attempt_count": attempt_count,
        "max_attempts": max_attempts,
        "next_retry_at": at if status == "retry_wait" else None,
        "lease_owner": lease_owner,
        "lease_expires_at": at if status == "running" else None,
        "lease_generation": lease_generation,
        "last_error_code": None,
        "last_error_message": None,
        "dead_letter_reason": None,
    }


def _result() -> BacktestResult:
    return BacktestResult(
        status="succeeded",
        input_hash="e" * 64,
        result_hash="f" * 64,
        assumptions=("orderbook_absent_uses_candle_close",),
        replay_events=(),
        trades=(),
        equity_points=(),
        metrics={"finalEquity": Decimal("1000")},
        golden_replay_signals=(),
    )
