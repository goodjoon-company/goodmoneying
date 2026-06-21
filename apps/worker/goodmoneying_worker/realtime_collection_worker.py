from __future__ import annotations

from goodmoneying_shared.time import now_kst
from goodmoneying_worker.collector import UpbitCollectionWorker
from goodmoneying_worker.runtime import (
    create_repository_from_environment,
    create_upbit_client_from_environment,
)


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
        raise
    worker.repository.record_collection_worker_heartbeat("realtime_collection", "running")
    print(f"실시간 수집 완료: rows={written}")
    return written


def main() -> None:
    worker = UpbitCollectionWorker(
        create_repository_from_environment(),
        create_upbit_client_from_environment(),
    )
    run_realtime_collection_once(worker)


if __name__ == "__main__":
    main()
