from __future__ import annotations

import logging
import os
import time

from goodmoneying_worker.aggregation_worker import CandleAggregationWorker
from goodmoneying_worker.runtime import (
    configure_logging_from_environment,
    create_heartbeat_repository_from_environment,
    create_repository_from_environment,
)

DEFAULT_AGGREGATION_POLL_SECONDS = 5.0
logger = logging.getLogger(__name__)


def poll_seconds_from_environment() -> float:
    value = os.getenv("GOODMONEYING_AGGREGATION_POLL_SECONDS")
    if value is None:
        return DEFAULT_AGGREGATION_POLL_SECONDS
    parsed = float(value)
    if parsed < 0:
        raise ValueError("GOODMONEYING_AGGREGATION_POLL_SECONDS는 0 이상의 값이어야 합니다.")
    return parsed


def run_aggregation_poll_loop(worker: CandleAggregationWorker, poll_seconds: float) -> None:
    try:
        try:
            with worker.heartbeat_lifecycle():
                while True:
                    completed = worker.run_once()
                    logger.info(
                        "aggregation_poll_completed targets=%s poll_seconds=%s",
                        completed,
                        poll_seconds,
                    )
                    time.sleep(poll_seconds)
        except Exception as exc:
            try:
                failed_heartbeat_recorded = worker.record_heartbeat(
                    "failed",
                    str(exc),
                )
            except Exception:
                logger.exception("aggregation_failed_heartbeat_failed")
            else:
                if not failed_heartbeat_recorded:
                    logger.error(
                        "aggregation_failed_heartbeat_skipped reason=in_flight"
                    )
            logger.exception("aggregation_poll_failed error=%s", type(exc).__name__)
            raise
    except KeyboardInterrupt:
        logger.info("aggregation_worker_stopped reason=keyboard_interrupt")


def main() -> None:
    configure_logging_from_environment()
    repository = create_repository_from_environment()
    run_aggregation_poll_loop(
        CandleAggregationWorker(
            repository,
            create_heartbeat_repository_from_environment(repository),
        ),
        poll_seconds_from_environment(),
    )


if __name__ == "__main__":
    main()
