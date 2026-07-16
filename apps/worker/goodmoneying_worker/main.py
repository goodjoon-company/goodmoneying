from __future__ import annotations

import argparse
import os
import time

from goodmoneying_worker.collector import UpbitCollectionWorker
from goodmoneying_worker.runtime import create_repository_from_environment
from goodmoneying_worker.upbit_client import LiveUpbitClient, UpbitClient


def create_upbit_client_from_environment() -> UpbitClient:
    if os.getenv("GOODMONEYING_LIVE_UPBIT") == "1":
        return LiveUpbitClient()
    raise RuntimeError(
        "운영 수집 런타임은 GOODMONEYING_LIVE_UPBIT=1 live 프로필만 허용한다. "
        "fixture 데이터는 테스트에서 클라이언트를 직접 주입할 때만 사용할 수 있다."
    )


def run_incremental_once(worker: UpbitCollectionWorker) -> int:
    worker.refresh_candidate_universe()
    written = worker.collect_incremental()
    print(f"수집 완료: rows={written}")
    return written


def non_negative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("0 이상의 값을 입력해야 합니다.")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(description="goodmoneying M1 수집 워커")
    parser.add_argument("--database", default=":memory:")
    incremental_mode = parser.add_mutually_exclusive_group()
    incremental_mode.add_argument("--once", action="store_true")
    incremental_mode.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-seconds", type=non_negative_float, default=60.0)
    parser.add_argument("--run-backfill-once", action="store_true")
    parser.add_argument("--run-backfill-loop", action="store_true")
    parser.add_argument("--backfill-poll-seconds", type=float, default=5.0)
    args = parser.parse_args()

    repository = create_repository_from_environment(args.database)
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
    if args.loop:
        try:
            while True:
                run_incremental_once(worker)
                time.sleep(args.interval_seconds)
        except KeyboardInterrupt:
            print("수집 루프를 종료합니다.")
            return
    run_incremental_once(worker)


if __name__ == "__main__":
    main()
