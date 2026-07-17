from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from goodmoneying_shared.data_foundation import MarketCatalogItem, MarketSyncResult
from goodmoneying_shared.models import FetchEvidence
from goodmoneying_worker import data_foundation_worker
from goodmoneying_worker.data_foundation_worker import (
    refresh_seconds_from_environment,
    run_market_sync_loop,
    run_market_sync_once,
)
from goodmoneying_worker.upbit_client import LiveUpbitClient, UpbitApiError


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


def test_market_sync_worker_records_empty_response_manifest_before_failing() -> None:
    repository = RecordingDataFoundationRepository()
    requested_at = datetime(2026, 7, 17, 1, tzinfo=UTC)
    evidence = FetchEvidence(
        endpoint="/v1/market/all",
        request_parameters={"is_details": "true"},
        requested_at=requested_at,
        responded_at=requested_at,
        response_status=200,
        response_payload=[],
        error_type="EmptyResponse",
        error_message="시장 목록이 비어 있다.",
    )

    with pytest.raises(ValueError, match="비어"):
        run_market_sync_once(
            repository,
            FailingCatalogClient(evidence),
            now=lambda: requested_at,
        )

    assert repository.failed_evidence == evidence


def test_market_sync_worker_records_http_failure_evidence_before_retrying() -> None:
    repository = RecordingDataFoundationRepository()
    requested_at = datetime(2026, 7, 17, 1, 1, tzinfo=UTC)
    evidence = FetchEvidence(
        endpoint="/v1/market/all",
        request_parameters={"is_details": "true"},
        requested_at=requested_at,
        responded_at=requested_at,
        response_status=429,
        response_payload={"error": {"message": "too many requests"}},
        error_type="HTTPStatusError",
        error_message="too many requests",
    )

    with pytest.raises(UpbitApiError):
        run_market_sync_once(
            repository,
            FailingApiCatalogClient(evidence),
            now=lambda: requested_at,
        )

    assert repository.failed_evidence == evidence


def test_market_sync_worker_records_current_non_array_success_response() -> None:
    repository = RecordingDataFoundationRepository()
    client = LiveUpbitClient(
        http_client=httpx.Client(
            base_url=LiveUpbitClient.BASE_URL,
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, json={"unexpected": "shape"})
            ),
        ),
        min_request_interval_seconds=0,
    )

    with pytest.raises(ValueError, match="JSON 배열"):
        run_market_sync_once(repository, client)

    assert repository.failed_evidence is not None
    assert repository.failed_evidence.response_status == 200
    assert repository.failed_evidence.response_payload == {"unexpected": "shape"}
    assert repository.failed_evidence.error_type == "UpbitResponseShapeError"
    assert repository.failed_evidence.error_message == "업비트 목록 응답이 JSON 배열이 아니다."


def test_market_sync_worker_preserves_raw_success_when_state_application_fails() -> None:
    repository = FailingSyncDataFoundationRepository()
    requested_at = datetime(2026, 7, 17, 1, 2, tzinfo=UTC)
    evidence = FetchEvidence(
        endpoint="/v1/market/all",
        request_parameters={"is_details": "true"},
        requested_at=requested_at,
        responded_at=requested_at,
        response_status=200,
        response_payload=[{"market": "KRW-DUPLICATE"}],
    )

    with pytest.raises(ValueError, match="중복 market_code"):
        run_market_sync_once(
            repository,
            SuccessfulEvidenceCatalogClient(evidence),
            now=lambda: requested_at,
        )

    assert repository.failed_evidence is not None
    assert repository.failed_evidence.response_payload == [{"market": "KRW-DUPLICATE"}]
    assert repository.failed_evidence.error_type == "ValueError"
    assert repository.failed_evidence.error_message == "중복 market_code"


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


def test_market_sync_loop_records_expected_failure_and_retries_next_cycle() -> None:
    repository = RecordingDataFoundationRepository()
    client = SequencedCatalogClient()
    sleeps: list[float] = []

    class StopLoop(BaseException):
        pass

    def sleep(delay: float) -> None:
        sleeps.append(delay)
        if len(sleeps) == 2:
            raise StopLoop

    with pytest.raises(StopLoop):
        run_market_sync_loop(repository, client, refresh_seconds=12, sleep=sleep)

    assert sleeps == [12, 12]
    assert repository.heartbeats == [
        ("failed", "업비트 목록 응답의 필드가 올바르지 않다."),
        ("running", None),
    ]


def test_market_sync_main_checks_data_foundation_contract_before_upbit_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class ReadyRepository:
        def assert_runtime_ready(self) -> None:
            calls.append("ready")

    repository = ReadyRepository()
    monkeypatch.setenv("GOODMONEYING_RUNTIME_MODE", "test")
    monkeypatch.setenv("GOODMONEYING_DATABASE_URL", "postgresql://example.invalid/goodmoneying")
    monkeypatch.setenv("GOODMONEYING_LIVE_UPBIT", "1")
    monkeypatch.setattr(
        data_foundation_worker,
        "PostgresDataFoundationRepository",
        lambda _url: repository,
    )
    monkeypatch.setattr(data_foundation_worker, "LiveUpbitClient", lambda: object())
    monkeypatch.setattr(
        data_foundation_worker,
        "run_market_sync_loop",
        lambda selected, _client, *, refresh_seconds: calls.append(
            f"loop:{selected is repository}:{refresh_seconds}"
        ),
    )

    data_foundation_worker.main()

    assert calls == ["ready", "loop:True:300.0"]


class CatalogClient:
    def __init__(self, catalog: list[MarketCatalogItem]) -> None:
        self.catalog = catalog

    def get_market_catalog(self) -> list[MarketCatalogItem]:
        return self.catalog


class SequencedCatalogClient:
    calls = 0

    def get_market_catalog(self) -> list[MarketCatalogItem]:
        self.calls += 1
        if self.calls == 1:
            raise ValueError("업비트 목록 응답의 필드가 올바르지 않다.")
        return [MarketCatalogItem("KRW-BTC", "비트코인", "Bitcoin", "NONE", True)]


class FailingCatalogClient:
    def __init__(self, evidence: FetchEvidence) -> None:
        self.last_market_catalog_evidence = evidence

    def get_market_catalog(self) -> list[MarketCatalogItem]:
        raise ValueError("업비트 시장 목록 성공 응답이 비어 있다.")


class FailingApiCatalogClient:
    def __init__(self, evidence: FetchEvidence) -> None:
        self.evidence = evidence

    def get_market_catalog(self) -> list[MarketCatalogItem]:
        raise UpbitApiError(
            429,
            "too many requests",
            evidence=self.evidence,
            error_type="HTTPStatusError",
        )


class SuccessfulEvidenceCatalogClient:
    def __init__(self, evidence: FetchEvidence) -> None:
        self.last_market_catalog_evidence = evidence

    def get_market_catalog(self) -> list[MarketCatalogItem]:
        return [MarketCatalogItem("KRW-DUPLICATE", "중복", "Duplicate", "NONE", True)]


class RecordingDataFoundationRepository:
    catalog: list[MarketCatalogItem]
    observed_at: datetime
    failed_evidence: FetchEvidence | None = None
    heartbeats: list[tuple[str, str | None]]

    def __init__(self) -> None:
        self.heartbeats = []

    def sync_market_catalog(
        self,
        catalog: list[MarketCatalogItem],
        *,
        observed_at: datetime,
        fetch_evidence: FetchEvidence | None = None,
    ) -> MarketSyncResult:
        del fetch_evidence
        self.catalog = catalog
        self.observed_at = observed_at
        return MarketSyncResult(len(catalog), len(catalog), 4, 1)

    def record_market_catalog_fetch_failure(self, fetch_evidence: FetchEvidence) -> None:
        self.failed_evidence = fetch_evidence

    def record_market_sync_heartbeat(
        self, status: str, error_message: str | None = None
    ) -> None:
        self.heartbeats.append((status, error_message))


class FailingSyncDataFoundationRepository(RecordingDataFoundationRepository):
    def sync_market_catalog(
        self,
        catalog: list[MarketCatalogItem],
        *,
        observed_at: datetime,
        fetch_evidence: FetchEvidence | None = None,
    ) -> MarketSyncResult:
        del catalog, observed_at, fetch_evidence
        raise ValueError("중복 market_code")
