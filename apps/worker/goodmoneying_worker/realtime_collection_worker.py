from __future__ import annotations

from goodmoneying_worker.collector import UpbitCollectionWorker
from goodmoneying_worker.runtime import (
    create_repository_from_environment,
    create_upbit_client_from_environment,
)


def run_realtime_collection_once(worker: UpbitCollectionWorker) -> int:
    worker.refresh_candidate_universe()
    written = worker.collect_incremental()
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
