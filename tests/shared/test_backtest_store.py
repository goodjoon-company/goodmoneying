from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from goodmoneying_shared.backtest_store import (
    BacktestCursorMismatchError,
    _cursor_digest,
    list_runs,
)


def test_백테스트_run_목록은_첫_페이지_상한을_cursor에_고정한다() -> None:
    repository = _BacktestListRepository()

    first_page = list_runs(repository, page_size=1, cursor=None)
    repository.rows.insert(0, _row(4))
    second_page = list_runs(repository, page_size=1, cursor=first_page["nextCursor"])

    assert [item["backtestRunId"] for item in first_page["items"]] == [3]
    assert first_page["nextCursor"] is not None
    assert [item["backtestRunId"] for item in second_page["items"]] == [2]
    assert repository.list_parameters[-1] == (3, 3, 2)


def test_백테스트_run_목록_cursor_문맥이_다르면_전용_예외를_반환한다() -> None:
    repository = _BacktestListRepository()

    with pytest.raises(BacktestCursorMismatchError):
        list_runs(repository, page_size=1, cursor="wrong-context")


def test_백테스트_run_목록_cursor는_공개_checksum_재계산_변조를_거부한다() -> None:
    repository = _BacktestListRepository()
    first_page = list_runs(repository, page_size=1, cursor=None)
    cursor = cast(str, first_page["nextCursor"])
    envelope = json.loads(base64.urlsafe_b64decode(_pad_base64(cursor)).decode())
    payload = {"ceiling": 999, "lastId": 999}
    envelope["payload"] = payload
    envelope["digest"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    tampered = base64.urlsafe_b64encode(
        json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
    ).decode().rstrip("=")

    with pytest.raises(BacktestCursorMismatchError):
        list_runs(repository, page_size=1, cursor=tampered)


def test_백테스트_run_목록_cursor는_bool_정수_값을_거부한다() -> None:
    repository = _BacktestListRepository()
    first_page = list_runs(repository, page_size=1, cursor=None)
    cursor = cast(str, first_page["nextCursor"])
    envelope = json.loads(base64.urlsafe_b64decode(_pad_base64(cursor)).decode())
    payload = {"ceiling": True, "lastId": True}
    envelope["payload"] = payload
    envelope["digest"] = _cursor_digest(payload)
    tampered = base64.urlsafe_b64encode(
        json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
    ).decode().rstrip("=")

    with pytest.raises(BacktestCursorMismatchError):
        list_runs(repository, page_size=1, cursor=tampered)


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


class _BacktestListConnection:
    def __init__(self, repository: _BacktestListRepository) -> None:
        self._repository = repository

    def __enter__(self) -> _BacktestListConnection:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, sql: str, parameters: tuple[object, ...] = ()) -> _Result:
        if "COALESCE(MAX(id), 0)" in sql:
            return _Result(row={"id": max(row["id"] for row in self._repository.rows)})
        if "FROM backtest_runs" in sql and "ORDER BY id DESC" in sql:
            ceiling, last_id, limit = cast(tuple[int, int, int], parameters)
            self._repository.list_parameters.append((ceiling, last_id, limit))
            rows = [
                row
                for row in self._repository.rows
                if int(row["id"]) <= ceiling and int(row["id"]) < last_id
            ][:limit]
            return _Result(rows=rows)
        raise AssertionError(sql)


class _BacktestListRepository:
    def __init__(self) -> None:
        self.rows = [_row(3), _row(2), _row(1)]
        self.list_parameters: list[tuple[int, int, int]] = []

    def _connect(self) -> _BacktestListConnection:
        return _BacktestListConnection(self)


def _row(row_id: int) -> dict[str, Any]:
    at = datetime(2026, 7, 18, row_id, tzinfo=UTC)
    return {
        "id": row_id,
        "strategy_version_id": 41,
        "dataset_version_id": 12,
        "engine_version": "backtest-core-v1",
        "status": "succeeded",
        "input_hash": "e" * 64,
        "result_hash": "f" * 64,
        "requested_at": at,
        "started_at": at,
        "finished_at": at,
    }


def _pad_base64(value: str) -> bytes:
    return (value + "=" * (-len(value) % 4)).encode()
