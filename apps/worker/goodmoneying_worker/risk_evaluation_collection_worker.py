from __future__ import annotations

import logging
import os
import time

from goodmoneying_shared.portfolio_bot_store import PostgresPortfolioBotStore
from goodmoneying_worker.runtime import (
    configure_logging_from_environment,
    create_repository_from_environment,
)

DEFAULT_RISK_EVALUATION_POLL_SECONDS = 2.0
logger = logging.getLogger(__name__)


def poll_seconds_from_environment() -> float:
    value = os.getenv("GOODMONEYING_RISK_EVALUATION_POLL_SECONDS")
    if value is None:
        return DEFAULT_RISK_EVALUATION_POLL_SECONDS
    parsed = float(value)
    if parsed < 0:
        raise ValueError("GOODMONEYING_RISK_EVALUATION_POLL_SECONDS는 0 이상의 값이어야 합니다.")
    return parsed


def run_risk_evaluation_poll_loop(
    store: PostgresPortfolioBotStore,
    poll_seconds: float,
) -> None:
    worker_id = os.getenv("GOODMONEYING_RISK_EVALUATION_WORKER_ID", "risk-evaluation-worker")
    try:
        while True:
            evaluated = store.evaluate_next_order_intent_risk(worker_id)
            logger.info(
                "risk_evaluation_poll_completed processed=%s poll_seconds=%s",
                1 if evaluated is not None else 0,
                poll_seconds,
            )
            time.sleep(poll_seconds)
    except KeyboardInterrupt:
        logger.info("risk_evaluation_worker_stopped reason=keyboard_interrupt")


def main() -> None:
    configure_logging_from_environment()
    repository = create_repository_from_environment()
    run_risk_evaluation_poll_loop(
        PostgresPortfolioBotStore(repository),
        poll_seconds_from_environment(),
    )


if __name__ == "__main__":
    main()
