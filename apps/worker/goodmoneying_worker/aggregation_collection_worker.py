from __future__ import annotations

import logging
import os
import time

from goodmoneying_worker.aggregation_worker import CandleAggregationWorker
from goodmoneying_worker.runtime import (
    configure_logging_from_environment,
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
        while True:
            worker._repository.record_collection_worker_heartbeat(
                "candle_aggregation", "running"
            )
            try:
                completed = worker.run_once()
            except Exception as exc:
                worker._repository.record_collection_worker_heartbeat(
                    "candle_aggregation", "failed", str(exc)
                )
                logger.exception("aggregation_poll_failed error=%s", type(exc).__name__)
                raise
            logger.info(
                "aggregation_poll_completed targets=%s poll_seconds=%s",
                completed,
                poll_seconds,
            )
            time.sleep(poll_seconds)
    except KeyboardInterrupt:
        logger.info("aggregation_worker_stopped reason=keyboard_interrupt")


def main() -> None:
    configure_logging_from_environment()
    run_aggregation_poll_loop(
        CandleAggregationWorker(create_repository_from_environment()),
        poll_seconds_from_environment(),
    )


if __name__ == "__main__":
    main()
