from __future__ import annotations

import os
import time

from goodmoneying_worker.collector import UpbitCollectionWorker
from goodmoneying_worker.runtime import (
    create_repository_from_environment,
    create_upbit_client_from_environment,
)

DEFAULT_BACKFILL_POLL_SECONDS = 10.0


def poll_seconds_from_environment() -> float:
    value = os.getenv("GOODMONEYING_BACKFILL_POLL_SECONDS")
    if value is None:
        return DEFAULT_BACKFILL_POLL_SECONDS
    parsed = float(value)
    if parsed < 0:
        raise ValueError("GOODMONEYING_BACKFILL_POLL_SECONDS는 0 이상의 값이어야 합니다.")
    return parsed


def run_backfill_poll_loop(
    worker: UpbitCollectionWorker,
    poll_seconds: float,
) -> None:
    try:
        while True:
            worker.repository.record_collection_worker_heartbeat(
                "backfill_collection",
                "running",
            )
            try:
                written = worker.run_backfill_once()
            except Exception as exc:
                worker.repository.record_collection_worker_heartbeat(
                    "backfill_collection",
                    "failed",
                    str(exc),
                )
                raise
            worker.repository.record_collection_worker_heartbeat(
                "backfill_collection",
                "running",
            )
            print(f"백필 수집 폴링 완료: rows={written}")
            time.sleep(poll_seconds)
    except KeyboardInterrupt:
        print("백필 수집 워커를 종료합니다.")


def main() -> None:
    worker = UpbitCollectionWorker(
        create_repository_from_environment(),
        create_upbit_client_from_environment(),
    )
    run_backfill_poll_loop(worker, poll_seconds_from_environment())


if __name__ == "__main__":
    main()
