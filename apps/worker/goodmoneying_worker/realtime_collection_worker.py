from __future__ import annotations

import logging
import os

from goodmoneying_shared.time import now_kst
from goodmoneying_worker.collector import UpbitCollectionWorker
from goodmoneying_worker.realtime_stream_worker import main as run_realtime_stream_worker
from goodmoneying_worker.runtime import (
    configure_logging_from_environment,
    create_repository_from_environment,
    create_upbit_client_from_environment,
)

logger = logging.getLogger(__name__)


def run_realtime_collection_once(worker: UpbitCollectionWorker) -> int:
    started_at = now_kst()
    worker.repository.record_collection_worker_heartbeat("realtime_collection", "running")
    try:
        worker.refresh_candidate_universe()
        written = worker.collect_incremental()
    except Exception as exc:
        worker.repository.record_collection_worker_heartbeat(
            "realtime_collection",
            "failed",
            str(exc),
        )
        worker.repository.record_collection_run_failure(
            "incremental",
            "ticker_snapshot",
            started_at,
            type(exc).__name__,
            str(exc),
        )
        logger.exception("realtime_collection_failed error=%s", type(exc).__name__)
        raise
    worker.repository.record_collection_worker_heartbeat("realtime_collection", "running")
    logger.info("realtime_collection_completed rows=%s", written)
    return written


def main() -> None:
    configure_logging_from_environment()
    if os.getenv("GOODMONEYING_LIVE_UPBIT") == "1":
        run_realtime_stream_worker()
        return
    worker = UpbitCollectionWorker(
        create_repository_from_environment(),
        create_upbit_client_from_environment(),
    )
    run_realtime_collection_once(worker)


if __name__ == "__main__":
    main()
