from __future__ import annotations

import argparse
import os
import time

from goodmoneying_shared.postgres_repository import PostgresOperationsRepository
from goodmoneying_shared.repository import OperationsRepository
from goodmoneying_shared.sqlite_repository import SQLiteOperationsRepository
from goodmoneying_worker.collector import UpbitCollectionWorker
from goodmoneying_worker.upbit_client import FixtureUpbitClient, LiveUpbitClient, UpbitClient


def create_upbit_client_from_environment() -> UpbitClient:
    if os.getenv("GOODMONEYING_LIVE_UPBIT") == "1":
        return LiveUpbitClient()
    return FixtureUpbitClient()


def main() -> None:
    parser = argparse.ArgumentParser(description="goodmoneying M1 수집 워커")
    parser.add_argument("--database", default=":memory:")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--run-backfill-once", action="store_true")
    parser.add_argument("--run-backfill-loop", action="store_true")
    parser.add_argument("--backfill-poll-seconds", type=float, default=5.0)
    args = parser.parse_args()

    database_url = os.getenv("GOODMONEYING_DATABASE_URL")
    repository: OperationsRepository
    if database_url and database_url.startswith(("postgres://", "postgresql://")):
        repository = PostgresOperationsRepository(database_url)
    else:
        repository = SQLiteOperationsRepository(args.database)
    worker = UpbitCollectionWorker(repository, create_upbit_client_from_environment())
    if args.run_backfill_once:
        written = worker.run_backfill_once()
        print(f"백필 실행 완료: rows={written}")
        return
    if args.run_backfill_loop:
        while True:
            written = worker.run_backfill_once()
            print(f"백필 폴링 완료: rows={written}")
            time.sleep(args.backfill_poll_seconds)
    worker.refresh_candidate_universe()
    written = worker.collect_incremental()
    print(f"수집 완료: rows={written}")


if __name__ == "__main__":
    main()
