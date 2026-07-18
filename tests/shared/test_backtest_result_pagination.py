from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

import pytest

from goodmoneying_shared.backtest_store import (
    BacktestCursorMismatchError,
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
