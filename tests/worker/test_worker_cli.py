from __future__ import annotations

import sys

import pytest

from goodmoneying_worker import main as worker_main


def test_worker_once_runs_single_collection(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class FakeWorker:
        def __init__(self, repository: object, client: object) -> None:
            pass

        def refresh_candidate_universe(self) -> None:
            calls.append("refresh")

        def collect_incremental(self) -> int:
            calls.append("collect")
            return 3

    monkeypatch.setattr(worker_main, "UpbitCollectionWorker", FakeWorker)
    monkeypatch.setattr(worker_main, "SQLiteOperationsRepository", lambda database: object())
    monkeypatch.setattr(worker_main, "FixtureUpbitClient", lambda: object())
    monkeypatch.setattr(sys, "argv", ["worker", "--once"])

    worker_main.main()

    assert calls == ["refresh", "collect"]


def test_worker_loop_runs_until_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class FakeWorker:
        def __init__(self, repository: object, client: object) -> None:
            pass

        def refresh_candidate_universe(self) -> None:
            calls.append("refresh")

        def collect_incremental(self) -> int:
            calls.append("collect")
            if calls.count("collect") == 2:
                raise KeyboardInterrupt
            return 3

    monkeypatch.setattr(worker_main, "UpbitCollectionWorker", FakeWorker)
    monkeypatch.setattr(worker_main, "SQLiteOperationsRepository", lambda database: object())
    monkeypatch.setattr(worker_main, "FixtureUpbitClient", lambda: object())
    monkeypatch.setattr("goodmoneying_worker.main.time.sleep", lambda seconds: None)
    monkeypatch.setattr(sys, "argv", ["worker", "--loop", "--interval-seconds", "0"])

    worker_main.main()

    assert calls == ["refresh", "collect", "refresh", "collect"]
