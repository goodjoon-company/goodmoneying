from __future__ import annotations

import pytest

from goodmoneying_worker import backfill_collection_worker, realtime_collection_worker


def test_realtime_collection_worker_runs_single_collection_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class FakeWorker:
        def __init__(self, repository: object, client: object) -> None:
            pass

        def refresh_candidate_universe(self) -> None:
            calls.append("refresh")

        def collect_incremental(self) -> int:
            calls.append("collect")
            return 3

    monkeypatch.setattr(realtime_collection_worker, "UpbitCollectionWorker", FakeWorker)
    monkeypatch.setattr(
        realtime_collection_worker,
        "create_repository_from_environment",
        lambda: object(),
    )
    monkeypatch.setattr(
        realtime_collection_worker,
        "create_upbit_client_from_environment",
        lambda: object(),
    )

    realtime_collection_worker.main()

    assert calls == ["refresh", "collect"]


def test_backfill_collection_worker_polls_backfill_jobs_every_ten_seconds_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class FakeWorker:
        def __init__(self, repository: object, client: object) -> None:
            pass

        def run_backfill_once(self) -> int:
            calls.append("backfill")
            if calls.count("backfill") == 2:
                raise KeyboardInterrupt
            return 0

    monkeypatch.delenv("GOODMONEYING_BACKFILL_POLL_SECONDS", raising=False)
    monkeypatch.setattr(backfill_collection_worker, "UpbitCollectionWorker", FakeWorker)
    monkeypatch.setattr(
        backfill_collection_worker,
        "create_repository_from_environment",
        lambda: object(),
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

    assert calls == ["backfill", "sleep:10", "backfill"]


def test_backfill_collection_worker_uses_env_poll_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class FakeWorker:
        def __init__(self, repository: object, client: object) -> None:
            pass

        def run_backfill_once(self) -> int:
            calls.append("backfill")
            raise KeyboardInterrupt

    monkeypatch.setenv("GOODMONEYING_BACKFILL_POLL_SECONDS", "2.5")
    monkeypatch.setattr(backfill_collection_worker, "UpbitCollectionWorker", FakeWorker)
    monkeypatch.setattr(
        backfill_collection_worker,
        "create_repository_from_environment",
        lambda: object(),
    )
    monkeypatch.setattr(
        backfill_collection_worker,
        "create_upbit_client_from_environment",
        lambda: object(),
    )

    backfill_collection_worker.main()

    assert backfill_collection_worker.poll_seconds_from_environment() == 2.5
    assert calls == ["backfill"]


def test_backfill_collection_worker_rejects_negative_poll_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOODMONEYING_BACKFILL_POLL_SECONDS", "-1")

    with pytest.raises(ValueError, match="0 이상의 값"):
        backfill_collection_worker.poll_seconds_from_environment()
