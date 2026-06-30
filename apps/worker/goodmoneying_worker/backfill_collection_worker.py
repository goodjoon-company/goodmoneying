from __future__ import annotations

import logging
import os
import time

from goodmoneying_worker.collector import UpbitCollectionWorker
from goodmoneying_worker.runtime import (
    configure_logging_from_environment,
    create_repository_from_environment,
    create_upbit_client_from_environment,
)

DEFAULT_BACKFILL_POLL_SECONDS = 10.0
DEFAULT_BACKFILL_BATCH_SIZE = 3000
logger = logging.getLogger(__name__)


def poll_seconds_from_environment() -> float:
    value = os.getenv("GOODMONEYING_BACKFILL_POLL_SECONDS")
    if value is None:
        return DEFAULT_BACKFILL_POLL_SECONDS
    parsed = float(value)
    if parsed < 0:
        raise ValueError("GOODMONEYING_BACKFILL_POLL_SECONDS는 0 이상의 값이어야 합니다.")
    return parsed


def batch_size_from_environment() -> int:
    value = os.getenv("GOODMONEYING_BACKFILL_BATCH_SIZE")
    if value is None:
        return DEFAULT_BACKFILL_BATCH_SIZE
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(
            "GOODMONEYING_BACKFILL_BATCH_SIZE는 1 이상의 정수 값이어야 합니다."
        ) from exc
    if str(parsed) != value or parsed < 1:
        raise ValueError("GOODMONEYING_BACKFILL_BATCH_SIZE는 1 이상의 정수 값이어야 합니다.")
    return parsed


def run_backfill_poll_loop(
    worker: UpbitCollectionWorker,
    poll_seconds: float,
) -> None:
    def record_running_heartbeat() -> None:
        worker.repository.record_collection_worker_heartbeat(
            "backfill_collection",
            "running",
        )

    try:
        while True:
            record_running_heartbeat()
            try:
                written = worker.run_backfill_once(on_progress=record_running_heartbeat)
            except Exception as exc:
                worker.repository.record_collection_worker_heartbeat(
                    "backfill_collection",
                    "failed",
                    str(exc),
                )
                logger.exception("backfill_poll_failed error=%s", type(exc).__name__)
                raise
            record_running_heartbeat()
            logger.info("backfill_poll_completed rows=%s poll_seconds=%s", written, poll_seconds)
            time.sleep(poll_seconds)
    except KeyboardInterrupt:
        logger.info("backfill_worker_stopped reason=keyboard_interrupt")


def main() -> None:
    configure_logging_from_environment()
    worker = UpbitCollectionWorker(
        create_repository_from_environment(),
        create_upbit_client_from_environment(),
        backfill_batch_size=batch_size_from_environment(),
    )
    run_backfill_poll_loop(worker, poll_seconds_from_environment())


if __name__ == "__main__":
    main()
