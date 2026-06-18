from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import sleep

import psycopg

REPO_ROOT = Path(__file__).resolve().parents[1]
for module_path in ("apps/api", "apps/worker", "packages/shared"):
    sys.path.insert(0, str(REPO_ROOT / module_path))

from goodmoneying_shared.postgres_repository import PostgresOperationsRepository  # noqa: E402
from goodmoneying_worker.collector import UpbitCollectionWorker  # noqa: E402
from goodmoneying_worker.upbit_client import LiveUpbitClient  # noqa: E402

JANUARY_2026_START = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
JANUARY_2026_END = datetime(2026, 2, 1, 0, 0, tzinfo=UTC)
DEFAULT_DATABASE_URL = "postgresql://goodmoneying:goodmoneying@127.0.0.1:5432/goodmoneying"


def main() -> int:
    parser = argparse.ArgumentParser(description="실제 업비트 2026년 1월 1분봉 백필 검증")
    parser.add_argument(
        "--database-url",
        default=os.getenv("GOODMONEYING_DATABASE_URL", DEFAULT_DATABASE_URL),
    )
    parser.add_argument("--request-interval-seconds", type=float, default=0.12)
    parser.add_argument("--poll-sleep-seconds", type=float, default=0.2)
    args = parser.parse_args()

    repository = PostgresOperationsRepository(args.database_url)
    client = LiveUpbitClient(min_request_interval_seconds=args.request_interval_seconds)
    worker = UpbitCollectionWorker(repository, client)

    print("실제 업비트 후보 유니버스를 갱신합니다.", flush=True)
    worker.refresh_candidate_universe()
    _, entries = repository.list_candidate_universe()
    top50_entries = entries[:50]
    top50_ids = [entry.instrument.id for entry in top50_entries]
    top50_markets = [entry.instrument.market_code for entry in top50_entries]
    print(f"현재 상위 50개: {', '.join(top50_markets[:10])} ...", flush=True)

    repository.update_active_targets(top50_ids, "실제 업비트 2026년 1월 백필 검증")
    stop_existing_backfills(repository)

    print("현재가, 호가, 최근 캔들 증분 수집으로 시장 리스트를 갱신합니다.", flush=True)
    incremental_rows = worker.collect_incremental()
    print(f"증분 수집 완료: rows={incremental_rows}", flush=True)

    plan = repository.create_backfill_plan(
        "source_candle",
        JANUARY_2026_START,
        JANUARY_2026_END,
        top50_ids,
    )
    job = repository.approve_backfill_job(plan.plan_id)
    print(
        "백필 작업 생성: "
        f"job_id={job.id}, 예상 요청={plan.estimated_request_count}, "
        f"예상 행={plan.estimated_row_count}",
        flush=True,
    )

    while True:
        current = next(item for item in repository.backfill_jobs() if item.id == job.id)
        targets = repository.backfill_job_targets(job.id)
        succeeded = sum(1 for target in targets if target.status == "succeeded")
        failed = sum(1 for target in targets if target.status == "failed")
        pending = sum(1 for target in targets if target.status in {"pending", "running"})
        print(
            f"진행률 {current.progress_percent}% "
            f"(성공 {succeeded}, 실패 {failed}, 남음 {pending})",
            flush=True,
        )
        if current.status in {"succeeded", "failed", "stopped"}:
            break
        rows = worker.run_backfill_once(target_limit=1)
        print(f"대상 1개 처리 완료: rows={rows}", flush=True)
        sleep(args.poll_sleep_seconds)

    row_summary = count_january_candles(args.database_url, top50_ids)
    failed_targets = [
        target for target in repository.backfill_job_targets(job.id) if target.status == "failed"
    ]
    print(
        "저장 결과: "
        f"rows={row_summary['rows']}, instruments={row_summary['instruments']}, "
        f"min={row_summary['min_time']}, max={row_summary['max_time']}",
        flush=True,
    )
    if failed_targets:
        for target in failed_targets:
            instrument = repository.get_instrument(target.instrument_id)
            market_code = instrument.market_code if instrument else str(target.instrument_id)
            print(
                f"실패 대상: {market_code} {target.error_code} {target.error_message}",
                flush=True,
            )
        return 1
    if row_summary["rows"] <= 0:
        print("2026년 1월 캔들이 저장되지 않았습니다.", flush=True)
        return 1
    return 0


def stop_existing_backfills(repository: PostgresOperationsRepository) -> None:
    for job in repository.backfill_jobs():
        if job.status in {"planned", "pending", "running", "paused"}:
            try:
                repository.control_backfill_job(job.id, "stop")
                print(f"기존 백필 작업 중지: #{job.id}", flush=True)
            except ValueError:
                pass


def count_january_candles(database_url: str, instrument_ids: list[int]) -> dict[str, object]:
    with psycopg.connect(database_url) as conn:
        row = conn.execute(
            """
            SELECT
              COUNT(*) AS rows,
              COUNT(DISTINCT instrument_id) AS instruments,
              MIN(candle_start_at) AS min_time,
              MAX(candle_start_at) AS max_time
            FROM source_candles
            WHERE instrument_id = ANY(%s)
              AND candle_unit = '1m'
              AND candle_start_at >= %s
              AND candle_start_at < %s
            """,
            (instrument_ids, JANUARY_2026_START, JANUARY_2026_END),
        ).fetchone()
    if row is None:
        return {"rows": 0, "instruments": 0, "min_time": None, "max_time": None}
    return {
        "rows": row[0],
        "instruments": row[1],
        "min_time": row[2],
        "max_time": row[3],
    }


if __name__ == "__main__":
    sys.exit(main())
