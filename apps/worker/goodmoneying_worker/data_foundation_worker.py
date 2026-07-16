from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from goodmoneying_shared.data_foundation import MarketCatalogItem, MarketSyncResult
from goodmoneying_shared.data_foundation_repository import (
    PostgresDataFoundationRepository,
)
from goodmoneying_worker.runtime import configure_logging_from_environment
from goodmoneying_worker.upbit_client import LiveUpbitClient, UpbitApiError

DEFAULT_MARKET_SYNC_INTERVAL_SECONDS = 300.0
logger = logging.getLogger(__name__)


class MarketCatalogClient(Protocol):
    def get_market_catalog(self) -> list[MarketCatalogItem]: ...


class DataFoundationSyncRepository(Protocol):
    def sync_market_catalog(
        self,
        catalog: list[MarketCatalogItem],
        *,
        observed_at: datetime,
    ) -> MarketSyncResult: ...


def refresh_seconds_from_environment() -> float:
    value = os.getenv("GOODMONEYING_MARKET_SYNC_INTERVAL_SECONDS")
    if value is None:
        return DEFAULT_MARKET_SYNC_INTERVAL_SECONDS
    parsed = float(value)
    if parsed < 0:
        raise ValueError("GOODMONEYING_MARKET_SYNC_INTERVAL_SECONDS는 0 이상이어야 합니다.")
    return parsed


def run_market_sync_once(
    repository: DataFoundationSyncRepository,
    client: MarketCatalogClient,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> MarketSyncResult:
    observed_at = now()
    result = repository.sync_market_catalog(
        client.get_market_catalog(),
        observed_at=observed_at,
    )
    logger.info(
        "market_sync_completed markets=%s history=%s targets=%s jobs=%s observed_at=%s",
        result.market_count,
        result.new_history_count,
        result.default_target_count,
        result.created_backfill_job_count,
        observed_at.isoformat(),
    )
    return result


def run_market_sync_loop(
    repository: DataFoundationSyncRepository,
    client: MarketCatalogClient,
    *,
    refresh_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    while True:
        try:
            run_market_sync_once(repository, client)
            sleep(refresh_seconds)
        except UpbitApiError as exc:
            delay = max(refresh_seconds, exc.retry_after_seconds or 1.0)
            logger.warning(
                "market_sync_rate_limited status=%s retry_after_seconds=%s",
                exc.status_code,
                delay,
            )
            sleep(delay)


def main() -> None:
    configure_logging_from_environment()
    database_url = os.getenv("GOODMONEYING_DATABASE_URL")
    if not database_url or not database_url.startswith(
        ("postgres://", "postgresql://")
    ):
        raise RuntimeError(
            "시장 동기화 작업자는 PostgreSQL "
            "GOODMONEYING_DATABASE_URL을 필요로 한다."
        )
    if os.getenv("GOODMONEYING_LIVE_UPBIT") != "1":
        raise RuntimeError("시장 동기화 작업자는 GOODMONEYING_LIVE_UPBIT=1에서만 실행한다.")
    run_market_sync_loop(
        PostgresDataFoundationRepository(database_url),
        LiveUpbitClient(),
        refresh_seconds=refresh_seconds_from_environment(),
    )


if __name__ == "__main__":
    main()
