from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from goodmoneying_api.schemas import (
    BackfillJobResponse,
    BackfillPlanResponse,
    CandidateUniverseEntryResponse,
    CandidateUniverseResponse,
    CandleResponse,
    CandleSeriesResponse,
    CollectionDashboardTargetResponse,
    CollectionDataStatusResponse,
    CollectionPlanResponse,
    CollectionRunResponse,
    CollectionRunsResponse,
    CollectionTargetsResponse,
    CoverageSegmentResponse,
    CoverageStatusResponse,
    DashboardSummaryResponse,
    DashboardTotalsResponse,
    HealthCheckResponse,
    InstrumentDetailResponse,
    InstrumentResponse,
    MarketListResponse,
    MarketListRowResponse,
    NotificationEventResponse,
    NotificationEventsResponse,
    OrderbookSummariesResponse,
    OrderbookSummaryResponse,
    TickerSnapshotResponse,
    TickerSnapshotsResponse,
)
from goodmoneying_shared.models import (
    BackfillJob,
    BackfillPlan,
    CandleView,
    CollectionDashboardTarget,
    CoverageStatus,
    DashboardSummary,
    Instrument,
    NotificationEvent,
    OrderbookSummary,
    TickerSnapshot,
    decimal_string,
)
from goodmoneying_shared.repository import OperationsRepository
from goodmoneying_shared.time import now_utc

CANDLE_UNITS = {"1m", "3m", "5m", "10m", "15m", "30m", "60m", "240m", "1d"}


class OperationsService:
    def __init__(self, repository: OperationsRepository) -> None:
        self._repository = repository

    def dashboard_summary(self) -> DashboardSummaryResponse:
        return dashboard_to_response(self._repository.dashboard_summary())

    def candidate_universe(self) -> CandidateUniverseResponse:
        ranked_at, entries = self._repository.list_candidate_universe()
        return CandidateUniverseResponse(
            rankedAt=ranked_at,
            entries=[
                CandidateUniverseEntryResponse(
                    instrument=instrument_to_response(entry.instrument),
                    rank=entry.rank,
                    accTradePrice24h=decimal_string(entry.acc_trade_price_24h) or "0",
                    accTradePrice24hDisplay=format_krw(entry.acc_trade_price_24h),
                    selected=entry.selected,
                    candidateStatus=entry.candidate_status,
                    qualityStatus="normal" if entry.rank <= 50 else "warning",
                    qualityDetail=quality_detail_for_rank(entry.rank, entry.selected),
                    collectionRangeDisplay="2024-01-01 00:00 KST ~ NOW",
                )
                for entry in entries
            ],
        )

    def update_collection_targets(
        self, instrument_ids: list[int], reason: str | None
    ) -> CollectionTargetsResponse:
        return CollectionTargetsResponse(
            targets=[
                instrument_to_response(item)
                for item in self._repository.update_active_targets(instrument_ids, reason)
            ]
        )

    def market_list(self) -> MarketListResponse:
        return MarketListResponse(
            rows=[
                MarketListRowResponse(
                    instrument=instrument_to_response(row.instrument),
                    tradePrice=decimal_string(row.trade_price) or "0",
                    accTradePrice24h=decimal_string(row.acc_trade_price_24h) or "0",
                    accTradePrice24hDisplay=format_krw(row.acc_trade_price_24h),
                    changeRate=decimal_string(row.change_rate) or "0",
                    tickerCollectedAt=row.ticker_collected_at,
                    orderbookCollectedAt=row.orderbook_collected_at,
                    qualityStatus=row.quality_status,
                    coveragePercent=decimal_string(row.coverage_percent) or "0",
                    storageBytes=row.storage_bytes,
                    storageBytesDisplay=row.storage_bytes_display,
                )
                for row in self._repository.market_list()
            ]
        )

    def instrument_detail(self, instrument_id: int) -> InstrumentDetailResponse | None:
        instrument = self._repository.get_instrument(instrument_id)
        ticker = self._repository.latest_ticker(instrument_id)
        orderbook = self._repository.latest_orderbook(instrument_id)
        if instrument is None or ticker is None or orderbook is None:
            return None
        return InstrumentDetailResponse(
            instrument=instrument_to_response(instrument),
            latestTicker=ticker_to_response(ticker),
            latestOrderbook=orderbook_to_response(orderbook),
            coverage=[
                coverage_to_response(item) for item in self._repository.coverage_for(instrument_id)
            ],
            duplicateRows24h=0,
            tickerFreshnessLabel=format_freshness_label(ticker.collected_at),
            orderbookFreshnessLabel=format_freshness_label(orderbook.collected_at),
        )

    def candles(
        self, instrument_id: int, unit: str, start_at: datetime, end_at: datetime
    ) -> CandleSeriesResponse:
        if unit not in CANDLE_UNITS:
            raise ValueError("지원하지 않는 캔들 단위다.")
        if start_at >= end_at:
            raise ValueError("캔들 조회 종료 시각은 시작 시각보다 뒤여야 한다.")
        return CandleSeriesResponse(
            unit=unit,
            candles=[
                candle_to_response(item)
                for item in self._repository.candles(instrument_id, unit, start_at, end_at)
            ],
        )

    def ticker_snapshots(
        self, instrument_id: int, start_at: datetime, end_at: datetime
    ) -> TickerSnapshotsResponse:
        return TickerSnapshotsResponse(
            items=[
                ticker_to_response(item)
                for item in self._repository.ticker_snapshots(instrument_id, start_at, end_at)
            ]
        )

    def orderbook_summaries(
        self, instrument_id: int, start_at: datetime, end_at: datetime
    ) -> OrderbookSummariesResponse:
        return OrderbookSummariesResponse(
            items=[
                orderbook_to_response(item)
                for item in self._repository.orderbook_summaries(instrument_id, start_at, end_at)
            ]
        )

    def collection_runs(self, limit: int) -> CollectionRunsResponse:
        return CollectionRunsResponse(
            items=[
                CollectionRunResponse(
                    id=item.id,
                    runType=item.run_type,
                    dataType=item.data_type,
                    status=item.status,
                    startedAt=item.started_at,
                    finishedAt=item.finished_at,
                )
                for item in self._repository.collection_runs(limit)
            ]
        )

    def create_backfill_plan(
        self,
        data_type: str,
        target_start_at: datetime,
        target_end_at: datetime,
        instrument_ids: list[int],
    ) -> BackfillPlanResponse:
        return backfill_plan_to_response(
            self._repository.create_backfill_plan(
                data_type,
                target_start_at,
                target_end_at,
                instrument_ids,
            )
        )

    def approve_backfill_job(self, plan_id: str) -> BackfillJobResponse:
        return backfill_job_to_response(self._repository.approve_backfill_job(plan_id))

    def control_backfill_job(self, job_id: int, action: str) -> BackfillJobResponse:
        return backfill_job_to_response(self._repository.control_backfill_job(job_id, action))

    def backfill_jobs(self) -> list[BackfillJobResponse]:
        return [backfill_job_to_response(item) for item in self._repository.backfill_jobs()]

    def notifications(self) -> NotificationEventsResponse:
        return NotificationEventsResponse(
            items=[
                notification_to_response(item) for item in self._repository.notification_events()
            ]
        )


def instrument_to_response(item: Instrument) -> InstrumentResponse:
    return InstrumentResponse(
        id=item.id,
        exchange=item.exchange,
        marketCode=item.market_code,
        quoteCurrency=item.quote_currency,
        baseAsset=item.base_asset,
        displayName=item.display_name,
    )


def coverage_to_response(item: CoverageStatus) -> CoverageStatusResponse:
    return CoverageStatusResponse(
        instrumentId=item.instrument_id,
        dataType=item.data_type,
        status=item.status,
        progressPercent=decimal_string(item.progress_percent) or "0",
        lastSuccessfulAt=item.last_successful_at,
    )


def notification_to_response(item: NotificationEvent) -> NotificationEventResponse:
    return NotificationEventResponse(
        id=item.id,
        severity=item.severity,
        eventType=item.event_type,
        title=item.title,
        message=item.message,
        status=item.status,
        createdAt=item.created_at,
    )


def dashboard_to_response(item: DashboardSummary) -> DashboardSummaryResponse:
    return DashboardSummaryResponse(
        status=item.status,
        refreshedAt=item.refreshed_at,
        totals=DashboardTotalsResponse(
            activeTargets=item.active_targets,
            activeTargetLimit=item.active_target_limit,
            normalTargets=item.normal_targets,
            warningTargets=item.warning_targets,
            incidentTargets=item.incident_targets,
            failedRuns24h=item.failed_runs_24h,
            failureRate24h=decimal_string(item.failure_rate_24h) or "0",
            delayedTargets=item.delayed_targets,
            missingRangesOpen=item.missing_ranges_open,
            storageBytesToday=item.storage_bytes_today,
            storageBytesTodayDisplay=item.storage_bytes_today_display,
            recentRequestCount=item.recent_request_count,
            rateLimitRemainingPercent=decimal_string(item.rate_limit_remaining_percent) or "0",
        ),
        coverage=[coverage_to_response(coverage) for coverage in item.coverage],
        targets=[dashboard_target_to_response(target) for target in item.targets],
        alerts=[notification_to_response(alert) for alert in item.alerts],
        healthChecks=[
            HealthCheckResponse(
                title=check.title,
                status=check.status,
                statusLabel=check.status_label,
                detail=check.detail,
            )
            for check in item.health_checks
        ],
    )


def dashboard_target_to_response(
    item: CollectionDashboardTarget,
) -> CollectionDashboardTargetResponse:
    target = item
    return CollectionDashboardTargetResponse(
        instrument=instrument_to_response(target.instrument),
        overallStatus=target.overall_status,
        overallStatusLabel=target.overall_status_label,
        plan=CollectionPlanResponse(
            instrumentId=target.plan.instrument_id,
            preset=target.plan.preset,
            rangeStartAt=target.plan.range_start_at,
            rangeEndAt=target.plan.range_end_at,
            isContinuous=target.plan.is_continuous,
            method=target.plan.method,
            displayRange=target.plan.display_range,
            rangeTimeZone=target.plan.range_time_zone,
            progressBasis=target.plan.progress_basis,
        ),
        dataStatuses=[
            CollectionDataStatusResponse(
                dataType=status.data_type,
                label=status.label,
                status=status.status,
                statusLabel=status.status_label,
                lastSuccessfulAt=status.last_successful_at,
                progressPercent=decimal_string(status.progress_percent) or "0",
                missingSegmentCount=status.missing_segment_count,
                storedRowCount=status.stored_row_count,
            )
            for status in target.data_statuses
        ],
        coverageSegments=[
            CoverageSegmentResponse(
                dataType=segment.data_type,
                status=segment.status,
                offsetPercent=decimal_string(segment.offset_percent) or "0",
                widthPercent=decimal_string(segment.width_percent) or "0",
                segmentStartAt=segment.segment_start_at,
                segmentEndAt=segment.segment_end_at,
                label=segment.label,
            )
            for segment in target.coverage_segments
        ],
    )


def ticker_to_response(item: TickerSnapshot) -> TickerSnapshotResponse:
    return TickerSnapshotResponse(
        bucketAt=item.bucket_at,
        tradePrice=decimal_string(item.trade_price) or "0",
        accTradePrice24h=decimal_string(item.acc_trade_price_24h) or "0",
        changeRate=decimal_string(item.change_rate) or "0",
        collectedAt=item.collected_at,
    )


def orderbook_to_response(item: OrderbookSummary) -> OrderbookSummaryResponse:
    return OrderbookSummaryResponse(
        bucketAt=item.bucket_at,
        bestBidPrice=decimal_string(item.best_bid_price) or "0",
        bestBidSize=decimal_string(item.best_bid_size) or "0",
        bestAskPrice=decimal_string(item.best_ask_price) or "0",
        bestAskSize=decimal_string(item.best_ask_size) or "0",
        spread=decimal_string(item.spread) or "0",
        bidDepth10=decimal_string(item.bid_depth_10) or "0",
        askDepth10=decimal_string(item.ask_depth_10) or "0",
        imbalance10=decimal_string(item.imbalance_10) or "0",
        collectedAt=item.collected_at,
    )


def candle_to_response(item: CandleView) -> CandleResponse:
    return CandleResponse(
        startedAt=item.started_at,
        open=decimal_string(item.open) or "0",
        high=decimal_string(item.high) or "0",
        low=decimal_string(item.low) or "0",
        close=decimal_string(item.close) or "0",
        volume=decimal_string(item.volume) or "0",
        tradeAmount=decimal_string(item.trade_amount) or "0",
        completeness=item.completeness,
    )


def backfill_plan_to_response(item: BackfillPlan) -> BackfillPlanResponse:
    return BackfillPlanResponse(
        planId=item.plan_id,
        dataType=item.data_type,
        estimatedRequestCount=item.estimated_request_count,
        estimatedRowCount=item.estimated_row_count,
        estimatedStorageBytes=item.estimated_storage_bytes,
        targets=item.targets,
    )


def backfill_job_to_response(item: BackfillJob) -> BackfillJobResponse:
    return BackfillJobResponse(
        id=item.id,
        status=item.status,
        dataType=item.data_type,
        progressPercent=decimal_string(item.progress_percent) or "0",
        createdAt=item.created_at,
    )


def format_freshness_label(value: datetime) -> str:
    age = now_utc() - value
    total_seconds = max(0, int(age.total_seconds()))
    if total_seconds < 60:
        return f"{total_seconds}초 전"
    if total_seconds < 3600:
        return f"{total_seconds // 60}분 전"
    return f"{total_seconds // 3600}시간 전"


def format_krw(value: object) -> str:
    return f"₩{int(Decimal(str(value))):,}"


def quality_detail_for_rank(rank: int, selected: bool) -> str:
    basis = "활성 수집 대상" if selected else "후보 대상"
    if rank <= 50:
        return f"품질 정상: 거래대금 순위 {rank}위, {basis}, 최신성/결측/저장량 기준 정상권"
    return f"품질 주의: 거래대금 순위 {rank}위, {basis}, 기본 수집 대상 밖이라 수동 확인 필요"
