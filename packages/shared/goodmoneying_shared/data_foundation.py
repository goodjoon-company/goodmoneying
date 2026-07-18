from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

CoverageState = Literal[
    "available",
    "no_trade",
    "missing",
    "unavailable",
    "unverified",
]
DurableJobStatus = Literal[
    "pending",
    "leased",
    "running",
    "retry_wait",
    "succeeded",
    "failed",
    "dead_letter",
    "cancelled",
]
CollectionDataType = Literal[
    "source_candle",
    "trade_event",
    "orderbook_snapshot",
    "ticker_snapshot",
]
CandleUnit = Literal["1m"]

ALLOWED_COLLECTION_DATA_TYPES: frozenset[str] = frozenset(
    {
        "source_candle",
        "trade_event",
        "orderbook_snapshot",
        "ticker_snapshot",
    }
)

DEFAULT_KRW_START_AT = datetime(2024, 1, 1, tzinfo=UTC)
INSTRUMENT_ADVISORY_LOCK_NAMESPACE = 0x474D494E
COVERAGE_ADVISORY_LOCK_NAMESPACE = 0x474D434F
ROLLUP_FRONTIER_ADVISORY_LOCK_NAMESPACE = 0x474D5246


@dataclass(frozen=True)
class MarketCatalogItem:
    market_code: str
    korean_name: str
    english_name: str
    market_warning: str
    tradable: bool
    market_event: dict[str, object] = field(default_factory=dict)

    @property
    def quote_currency(self) -> str:
        return self.market_code.partition("-")[0]

    @property
    def base_asset(self) -> str:
        return self.market_code.partition("-")[2]


@dataclass(frozen=True)
class DefaultCollectionTarget:
    market_code: str
    data_type: CollectionDataType
    candle_unit: str | None
    start_at: datetime
    continuous: bool
    retention_days: int | None
    priority: int


@dataclass(frozen=True)
class CoverageEvidence:
    before_listing: bool = False
    after_trading_end: bool = False
    outside_source_retention: bool = False
    source_row_count: int = 0
    manifest_checksum: str | None = None
    request_succeeded: bool = False
    no_trade_corroborated: bool = False
    attempted: bool = False
    retry_budget_exhausted: bool = False
    market_trading_resumed: bool = False


@dataclass(frozen=True)
class DurableJobState:
    status: DurableJobStatus
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None


@dataclass(frozen=True)
class MarketSyncResult:
    market_count: int
    new_history_count: int
    default_target_count: int
    created_backfill_job_count: int


@dataclass(frozen=True)
class MarketStatusRevision:
    market_code: str
    trading_status: Literal["active", "inactive", "delisted", "unknown"]
    market_warning: str
    valid_from: datetime
    valid_to: datetime | None
    observed_at: datetime


@dataclass(frozen=True)
class MarketCollectionPolicySettings:
    start_at: datetime
    data_types: tuple[CollectionDataType, ...]
    candle_unit: CandleUnit
    retention_days: int | None
    priority: int
    continuous: bool

    def validate(self, *, changed_at: datetime) -> None:
        _require_utc(changed_at, "changed_at")
        _require_utc(self.start_at, "start_at")
        if self.start_at >= changed_at:
            raise ValueError("정책 시작 시각은 변경 시각보다 이전이어야 한다.")
        if not self.data_types:
            raise ValueError("수집 데이터 유형은 최소 한 개 이상이어야 한다.")
        if len(self.data_types) != len(set(self.data_types)):
            raise ValueError("수집 데이터 유형은 중복될 수 없다.")
        if not set(self.data_types).issubset(ALLOWED_COLLECTION_DATA_TYPES):
            raise ValueError("지원하지 않는 수집 데이터 유형이 포함되어 있다.")
        if self.candle_unit != "1m":
            raise ValueError("현재 백필 워커가 지원하는 캔들 주기는 1m뿐이다.")
        if self.retention_days is not None and not 1 <= self.retention_days <= 36_500:
            raise ValueError("보존 기간은 1일 이상 36500일 이하여야 한다.")
        if not 1 <= self.priority <= 1000:
            raise ValueError("우선순위는 1 이상 1000 이하여야 한다.")


@dataclass(frozen=True)
class MarketCollectionStatus:
    instrument_id: int
    market_code: str
    korean_name: str
    english_name: str
    quote_currency: str
    trading_status: Literal["active", "inactive", "delisted", "unknown"]
    market_warning: str
    target_status: Literal["active", "paused", "excluded", "not_targeted"]
    active_data_type_count: int
    total_data_type_count: int
    coverage_counts: dict[CoverageState, int]
    collection_policy: MarketCollectionPolicySettings | None = None


@dataclass(frozen=True)
class DataFoundationOverview:
    market_count: int
    krw_market_count: int
    active_target_count: int
    pending_backfill_job_count: int
    desired_subscription_count: int
    policy_start_at: datetime
    coverage_counts: dict[CoverageState, int]
    markets: list[MarketCollectionStatus]


@dataclass(frozen=True)
class LeasedBackfillJob:
    id: int
    idempotency_key: str
    lease_owner: str
    lease_expires_at: datetime
    attempt_count: int
    max_attempts: int
    target_start_at: datetime
    target_end_at: datetime


@dataclass(frozen=True)
class CollectionSubscriptionDesire:
    target_spec_id: int
    market_code: str
    desired_state: Literal["subscribed", "unsubscribed"]
    generation: int
    target_status: Literal["active", "paused", "excluded"]
    trading_status: Literal["active", "inactive", "delisted", "unknown"]
    data_type: CollectionDataType
    continuous: bool


def _require_utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{name}은 UTC timezone-aware datetime이어야 한다.")


def build_default_krw_targets(
    market: MarketCatalogItem,
    *,
    observed_at: datetime,
) -> list[DefaultCollectionTarget]:
    """모든 KRW 마켓에 적용하는 초기 수집 정책을 결정론적으로 생성한다."""

    _require_utc(observed_at, "observed_at")
    if market.quote_currency != "KRW":
        return []
    specifications: tuple[tuple[CollectionDataType, str | None], ...] = (
        ("source_candle", "1m"),
        ("trade_event", None),
        ("orderbook_snapshot", None),
        ("ticker_snapshot", None),
    )
    return [
        DefaultCollectionTarget(
            market_code=market.market_code,
            data_type=data_type,
            candle_unit=candle_unit,
            start_at=DEFAULT_KRW_START_AT,
            continuous=True,
            retention_days=None,
            priority=100,
        )
        for data_type, candle_unit in specifications
    ]


def classify_coverage(evidence: CoverageEvidence) -> CoverageState:
    """원천 증거를 합성하지 않고 시간 구간의 상태를 판정한다."""

    if (
        evidence.before_listing
        or evidence.outside_source_retention
        or (evidence.after_trading_end and not evidence.market_trading_resumed)
    ):
        return "unavailable"
    if evidence.source_row_count > 0 and evidence.manifest_checksum:
        return "available"
    if evidence.request_succeeded and evidence.no_trade_corroborated:
        return "no_trade"
    if evidence.attempted and evidence.retry_budget_exhausted:
        return "missing"
    return "unverified"


def internal_minute_candle_gaps(
    *,
    requested_start_at: datetime,
    requested_end_at: datetime,
    candle_starts: tuple[datetime, ...],
) -> tuple[tuple[datetime, datetime], ...]:
    """성공한 분 캔들 페이지의 내부 공백을 반환한다."""

    _require_utc(requested_start_at, "requested_start_at")
    _require_utc(requested_end_at, "requested_end_at")
    if requested_start_at >= requested_end_at:
        raise ValueError("분 캔들 요청 종료 시각은 시작 시각보다 뒤여야 한다.")
    minute = timedelta(minutes=1)
    bounded_starts = sorted(
        {
            started_at
            for started_at in candle_starts
            if requested_start_at <= started_at < requested_end_at
        }
    )
    for started_at in bounded_starts:
        _require_utc(started_at, "candle_starts")
    gaps: list[tuple[datetime, datetime]] = []
    for previous, following in zip(bounded_starts, bounded_starts[1:], strict=False):
        gap_start = previous + minute
        if gap_start < following:
            gaps.append((gap_start, following))
    return tuple(gaps)


def can_claim_job(job: DurableJobState, *, now: datetime) -> bool:
    """대기 작업 또는 임대가 만료된 작업만 다른 작업자가 회수하게 한다."""

    _require_utc(now, "now")
    if job.status in {"pending", "retry_wait"}:
        return True
    if job.status not in {"leased", "running"} or job.lease_expires_at is None:
        return False
    _require_utc(job.lease_expires_at, "lease_expires_at")
    return job.lease_expires_at <= now
