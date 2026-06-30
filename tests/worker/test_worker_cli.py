from __future__ import annotations

import logging
from collections.abc import Callable

import pytest

from goodmoneying_worker import (
    backfill_collection_worker,
    realtime_collection_worker,
    runtime,
)


class FakeRepository:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def record_collection_worker_heartbeat(
        self,
        worker_type: str,
        status: str,
        error_message: str | None = None,
    ) -> None:
        self._calls.append(f"heartbeat:{worker_type}:{status}")

    def record_collection_run_failure(
        self,
        run_type: str,
        data_type: str,
        started_at: object,
        error_code: str,
        error_message: str,
    ) -> None:
        self._calls.append(f"failure:{run_type}:{data_type}:{error_code}")


def test_realtime_collection_worker_runs_single_collection_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class FakeWorker:
        def __init__(
            self,
            repository: object,
            client: object,
            backfill_batch_size: int = 3000,
        ) -> None:
            self.repository = repository

        def refresh_candidate_universe(self) -> None:
            calls.append("refresh")

        def collect_incremental(self) -> int:
            calls.append("collect")
            return 3

    monkeypatch.setattr(realtime_collection_worker, "UpbitCollectionWorker", FakeWorker)
    monkeypatch.setattr(
        realtime_collection_worker,
        "create_repository_from_environment",
        lambda: FakeRepository(calls),
    )
    monkeypatch.setattr(
        realtime_collection_worker,
        "create_upbit_client_from_environment",
        lambda: object(),
    )

    realtime_collection_worker.main()

    assert calls == [
        "heartbeat:realtime_collection:running",
        "refresh",
        "collect",
        "heartbeat:realtime_collection:running",
    ]


def test_realtime_collection_worker_uses_websocket_stream_in_live_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setenv("GOODMONEYING_LIVE_UPBIT", "1")
    monkeypatch.setattr(
        realtime_collection_worker,
        "run_realtime_stream_worker",
        lambda: calls.append("stream"),
    )

    realtime_collection_worker.main()

    assert calls == ["stream"]


def test_backfill_collection_worker_polls_backfill_jobs_every_ten_seconds_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class FakeWorker:
        def __init__(
            self,
            repository: object,
            client: object,
            backfill_batch_size: int = 3000,
        ) -> None:
            self.repository = repository

        def run_backfill_once(self, on_progress: Callable[[], object] | None = None) -> int:
            calls.append("backfill")
            if on_progress is not None:
                on_progress()
            if calls.count("backfill") == 2:
                raise KeyboardInterrupt
            return 0

    monkeypatch.delenv("GOODMONEYING_BACKFILL_POLL_SECONDS", raising=False)
    monkeypatch.setattr(backfill_collection_worker, "UpbitCollectionWorker", FakeWorker)
    monkeypatch.setattr(
        backfill_collection_worker,
        "create_repository_from_environment",
        lambda: FakeRepository(calls),
    )
    monkeypatch.setattr(
        backfill_collection_worker,
        "create_upbit_client_from_environment",
        lambda: object(),
    )
    monkeypatch.setattr(
        "goodmoneying_worker.backfill_collection_worker.time.sleep",
        lambda seconds: calls.append(f"sleep:{seconds:g}"),
    )

    backfill_collection_worker.main()

    assert calls == [
        "heartbeat:backfill_collection:running",
        "backfill",
        "heartbeat:backfill_collection:running",
        "heartbeat:backfill_collection:running",
        "sleep:10",
        "heartbeat:backfill_collection:running",
        "backfill",
        "heartbeat:backfill_collection:running",
    ]


def test_backfill_collection_worker_uses_env_poll_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class FakeWorker:
        def __init__(
            self,
            repository: object,
            client: object,
            backfill_batch_size: int = 3000,
        ) -> None:
            self.repository = repository

        def run_backfill_once(self, on_progress: Callable[[], object] | None = None) -> int:
            calls.append("backfill")
            if on_progress is not None:
                on_progress()
            raise KeyboardInterrupt

    monkeypatch.setenv("GOODMONEYING_BACKFILL_POLL_SECONDS", "2.5")
    monkeypatch.setattr(backfill_collection_worker, "UpbitCollectionWorker", FakeWorker)
    monkeypatch.setattr(
        backfill_collection_worker,
        "create_repository_from_environment",
        lambda: FakeRepository(calls),
    )
    monkeypatch.setattr(
        backfill_collection_worker,
        "create_upbit_client_from_environment",
        lambda: object(),
    )

    backfill_collection_worker.main()

    assert backfill_collection_worker.poll_seconds_from_environment() == 2.5
    assert calls == [
        "heartbeat:backfill_collection:running",
        "backfill",
        "heartbeat:backfill_collection:running",
    ]


def test_backfill_collection_worker_uses_default_batch_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GOODMONEYING_BACKFILL_BATCH_SIZE", raising=False)

    assert backfill_collection_worker.batch_size_from_environment() == 3000


def test_backfill_collection_worker_uses_env_batch_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOODMONEYING_BACKFILL_BATCH_SIZE", "500")

    assert backfill_collection_worker.batch_size_from_environment() == 500


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "abc"])
def test_backfill_collection_worker_rejects_invalid_batch_size(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("GOODMONEYING_BACKFILL_BATCH_SIZE", value)

    with pytest.raises(ValueError, match="1 이상의 정수"):
        backfill_collection_worker.batch_size_from_environment()


def test_backfill_collection_worker_rejects_negative_poll_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOODMONEYING_BACKFILL_POLL_SECONDS", "-1")

    with pytest.raises(ValueError, match="0 이상의 값"):
        backfill_collection_worker.poll_seconds_from_environment()


def test_worker_logging_uses_info_level_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOODMONEYING_LOG_LEVEL", raising=False)

    assert runtime.log_level_from_environment() == logging.INFO


def test_worker_logging_uses_env_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOODMONEYING_LOG_LEVEL", "debug")

    assert runtime.log_level_from_environment() == logging.DEBUG


def test_worker_logging_rejects_invalid_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOODMONEYING_LOG_LEVEL", "TRACE")

    with pytest.raises(ValueError, match="GOODMONEYING_LOG_LEVEL"):
        runtime.log_level_from_environment()
