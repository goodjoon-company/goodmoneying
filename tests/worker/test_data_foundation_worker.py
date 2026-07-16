from __future__ import annotations

from datetime import UTC, datetime

import pytest

from goodmoneying_shared.data_foundation import MarketCatalogItem, MarketSyncResult
from goodmoneying_worker.data_foundation_worker import (
    refresh_seconds_from_environment,
    run_market_sync_once,
)


def test_market_sync_worker_passes_full_catalog_and_utc_time() -> None:
    repository = RecordingDataFoundationRepository()
    catalog = [
        MarketCatalogItem("KRW-BTC", "비트코인", "Bitcoin", "NONE", True),
        MarketCatalogItem("BTC-ETH", "이더리움", "Ethereum", "CAUTION", False),
    ]
    now = datetime(2026, 7, 17, tzinfo=UTC)

    result = run_market_sync_once(
        repository,
        CatalogClient(catalog),
        now=lambda: now,
    )

    assert result.market_count == 2
    assert repository.catalog == catalog
    assert repository.observed_at == now


def test_market_sync_refresh_interval_defaults_and_validates_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GOODMONEYING_MARKET_SYNC_INTERVAL_SECONDS", raising=False)
    assert refresh_seconds_from_environment() == 300

    monkeypatch.setenv("GOODMONEYING_MARKET_SYNC_INTERVAL_SECONDS", "0")
    assert refresh_seconds_from_environment() == 0

    monkeypatch.setenv("GOODMONEYING_MARKET_SYNC_INTERVAL_SECONDS", "-1")
    with pytest.raises(ValueError, match="0 이상"):
        refresh_seconds_from_environment()


class CatalogClient:
    def __init__(self, catalog: list[MarketCatalogItem]) -> None:
        self.catalog = catalog

    def get_market_catalog(self) -> list[MarketCatalogItem]:
        return self.catalog


class RecordingDataFoundationRepository:
    catalog: list[MarketCatalogItem]
    observed_at: datetime

    def sync_market_catalog(
        self,
        catalog: list[MarketCatalogItem],
        *,
        observed_at: datetime,
    ) -> MarketSyncResult:
        self.catalog = catalog
        self.observed_at = observed_at
        return MarketSyncResult(len(catalog), len(catalog), 4, 1)
