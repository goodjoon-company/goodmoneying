from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from goodmoneying_shared.postgres_repository import PostgresOperationsRepository
from goodmoneying_shared.sqlite_repository import SQLiteOperationsRepository
from goodmoneying_worker import (
    aggregation_collection_worker,
    aggregation_worker,
    backfill_collection_worker,
    realtime_collection_worker,
    runtime,
)
from goodmoneying_worker.aggregation_worker import HEARTBEAT_THREAD_NAME


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


def test_집계_워커는_하트비트를_기록하고_기본_5초_주기로_실행한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class FakeAggregationWorker:
        def __init__(
            self,
            repository: FakeRepository,
            heartbeat_repository: FakeRepository,
        ) -> None:
            self._repository = repository
            self._heartbeat_repository = heartbeat_repository

        def record_heartbeat(
            self, status: str, error_message: str | None = None
        ) -> None:
            self._heartbeat_repository.record_collection_worker_heartbeat(
                "candle_aggregation", status, error_message
            )

        @contextmanager
        def heartbeat_lifecycle(self) -> Iterator[None]:
            self.record_heartbeat("running")
            yield

        def run_once(self) -> int:
            calls.append("aggregate")
            raise KeyboardInterrupt

    monkeypatch.delenv("GOODMONEYING_AGGREGATION_POLL_SECONDS", raising=False)
    monkeypatch.setattr(
        aggregation_collection_worker,
        "CandleAggregationWorker",
        FakeAggregationWorker,
    )
    repository = FakeRepository(calls)
    heartbeat_repository = FakeRepository(calls)
    monkeypatch.setattr(
        aggregation_collection_worker,
        "create_repository_from_environment",
        lambda: repository,
    )
    monkeypatch.setattr(
        aggregation_collection_worker,
        "create_heartbeat_repository_from_environment",
        lambda source_repository: (
            heartbeat_repository
            if source_repository is repository
            else pytest.fail("주 저장소를 기준으로 하트비트 저장소를 만들어야 한다.")
        ),
    )

    aggregation_collection_worker.main()

    assert calls == ["heartbeat:candle_aggregation:running", "aggregate"]
    assert aggregation_collection_worker.poll_seconds_from_environment() == 5.0


def test_집계_heartbeat_저장소는_postgres와_sqlite별_2초_제한을_사용한다(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "GOODMONEYING_DATABASE_URL", "postgresql://example.invalid/goodmoneying"
    )

    postgres_repository = runtime.create_heartbeat_repository_from_environment()

    assert isinstance(postgres_repository, PostgresOperationsRepository)
    assert postgres_repository._connect_and_statement_timeout_seconds == 2.0

    monkeypatch.delenv("GOODMONEYING_DATABASE_URL")
    source_repository = SQLiteOperationsRepository.from_path(
        tmp_path / "heartbeat.sqlite3"
    )
    sqlite_repository = runtime.create_heartbeat_repository_from_environment(
        source_repository
    )

    assert isinstance(sqlite_repository, SQLiteOperationsRepository)
    assert sqlite_repository is not source_repository
    assert sqlite_repository._database_url == source_repository._database_url
    busy_timeout = sqlite_repository._execute("PRAGMA busy_timeout").fetchone()[0]
    assert busy_timeout == 2_000

    in_memory_repository = SQLiteOperationsRepository()

    assert (
        runtime.create_heartbeat_repository_from_environment(in_memory_repository)
        is in_memory_repository
    )


def test_집계_폴링을_3회_반복해도_차단된_heartbeat_스레드는_하나만_유지한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callback_started = threading.Event()
    release_callback = threading.Event()

    class RepeatedJobRepository:
        def __init__(self) -> None:
            self.claim_count = 0

        def schedule_candle_aggregation(self) -> None:
            return None

        def claim_next_candle_aggregation_job(self) -> SimpleNamespace:
            self.claim_count += 1
            if self.claim_count > 3:
                raise KeyboardInterrupt
            return SimpleNamespace(id=self.claim_count)

        def candle_aggregation_job_targets(self, job_id: int) -> list[object]:
            return []

        def record_collection_worker_heartbeat(
            self,
            worker_type: str,
            status: str,
            error_message: str | None = None,
        ) -> None:
            if threading.current_thread().name == HEARTBEAT_THREAD_NAME:
                callback_started.set()
                release_callback.wait()

    real_runner = aggregation_worker.PeriodicHeartbeatRunner

    def fast_shutdown_runner(
        heartbeat: Callable[[], None], interval_seconds: float
    ) -> aggregation_worker.PeriodicHeartbeatRunner:
        return real_runner(
            heartbeat,
            interval_seconds,
            shutdown_grace_seconds=0.01,
        )

    monkeypatch.setattr(
        aggregation_worker,
        "PeriodicHeartbeatRunner",
        fast_shutdown_runner,
    )
    repository: Any = RepeatedJobRepository()
    worker = aggregation_worker.CandleAggregationWorker(repository, repository)

    started = time.monotonic()
    aggregation_collection_worker.run_aggregation_poll_loop(worker, poll_seconds=0)
    elapsed = time.monotonic() - started

    try:
        assert elapsed < 0.2
        assert callback_started.wait(timeout=0.1)
        blocked_threads = [
            thread
            for thread in threading.enumerate()
            if thread.name == HEARTBEAT_THREAD_NAME
        ]
        assert len(blocked_threads) == 1
        assert blocked_threads[0].daemon is True
    finally:
        release_callback.set()
        for thread in threading.enumerate():
            if thread.name == HEARTBEAT_THREAD_NAME:
                thread.join(timeout=1)


def test_정상_집계_폴링이_끝나면_heartbeat_스레드가_남지_않는다() -> None:
    class SingleJobRepository:
        def __init__(self) -> None:
            self.claim_count = 0

        def schedule_candle_aggregation(self) -> None:
            return None

        def claim_next_candle_aggregation_job(self) -> SimpleNamespace:
            self.claim_count += 1
            if self.claim_count > 1:
                raise KeyboardInterrupt
            return SimpleNamespace(id=self.claim_count)

        def candle_aggregation_job_targets(self, job_id: int) -> list[object]:
            return []

        def record_collection_worker_heartbeat(
            self,
            worker_type: str,
            status: str,
            error_message: str | None = None,
        ) -> None:
            return None

    repository: Any = SingleJobRepository()
    worker = aggregation_worker.CandleAggregationWorker(repository, repository)

    aggregation_collection_worker.run_aggregation_poll_loop(worker, poll_seconds=0)

    assert all(
        thread.name != HEARTBEAT_THREAD_NAME for thread in threading.enumerate()
    )


def test_차단된_heartbeat_중_집계가_3회_실패해도_소유자는_유한_시간에_반환한다(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    callback_started = threading.Event()
    release_callback = threading.Event()
    heartbeat_calls: list[tuple[str, str]] = []
    caught_errors: list[RuntimeError] = []

    class FailingJobRepository:
        def schedule_candle_aggregation(self) -> None:
            return None

        def claim_next_candle_aggregation_job(self) -> None:
            assert callback_started.wait(timeout=0.1)
            raise RuntimeError("집계 실패")

        def record_collection_worker_heartbeat(
            self,
            worker_type: str,
            status: str,
            error_message: str | None = None,
        ) -> None:
            heartbeat_calls.append((threading.current_thread().name, status))
            callback_started.set()
            release_callback.wait()

    real_runner = aggregation_worker.PeriodicHeartbeatRunner

    def fast_shutdown_runner(
        heartbeat: Callable[[], None], interval_seconds: float
    ) -> aggregation_worker.PeriodicHeartbeatRunner:
        return real_runner(
            heartbeat,
            interval_seconds,
            shutdown_grace_seconds=0.01,
        )

    monkeypatch.setattr(
        aggregation_worker,
        "PeriodicHeartbeatRunner",
        fast_shutdown_runner,
    )
    repository: Any = FailingJobRepository()
    worker = aggregation_worker.CandleAggregationWorker(repository, repository)

    def run_repeated_errors() -> None:
        for _ in range(3):
            try:
                aggregation_collection_worker.run_aggregation_poll_loop(
                    worker,
                    poll_seconds=0,
                )
            except RuntimeError as exc:
                caught_errors.append(exc)

    owner = threading.Thread(target=run_repeated_errors)
    with caplog.at_level("ERROR"):
        owner.start()
        owner.join(timeout=0.2)
    owner_returned = owner.is_alive() is False
    blocked_threads = [
        thread
        for thread in threading.enumerate()
        if thread.name == HEARTBEAT_THREAD_NAME
    ]
    heartbeat_call_count_before_release = len(heartbeat_calls)

    release_callback.set()
    owner.join(timeout=1)
    for thread in blocked_threads:
        thread.join(timeout=1)

    assert owner_returned is True
    assert len(caught_errors) == 3
    assert heartbeat_call_count_before_release == 1
    assert len(blocked_threads) <= 1
    assert any(
        message.startswith("aggregation_failed_heartbeat_skipped")
        for message in caplog.messages
    )


def test_heartbeat가_차단되지_않으면_집계_실패_상태를_저장한다() -> None:
    class FailingJobRepository:
        def schedule_candle_aggregation(self) -> None:
            return None

        def claim_next_candle_aggregation_job(self) -> None:
            raise RuntimeError("집계 실패")

    repository: Any = FailingJobRepository()
    heartbeat_repository = SQLiteOperationsRepository()
    worker = aggregation_worker.CandleAggregationWorker(
        repository,
        heartbeat_repository,
    )

    with pytest.raises(RuntimeError, match="집계 실패"):
        aggregation_collection_worker.run_aggregation_poll_loop(worker, poll_seconds=0)

    runtime_status = heartbeat_repository.collection_worker_runtime_status(
        "candle_aggregation"
    )
    assert runtime_status.status == "failed"
    assert runtime_status.status_detail == "집계 실패"
    assert all(
        thread.name != HEARTBEAT_THREAD_NAME for thread in threading.enumerate()
    )


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
