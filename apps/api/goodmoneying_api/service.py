from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from goodmoneying_api.dashboard_refresh import DEFAULT_DASHBOARD_REFRESH_SECONDS
from goodmoneying_api.schemas import (
    AuditLogSummaryResponse,
    BackfillJobResponse,
    BackfillPlanResponse,
    BackfillWorkerStatusResponse,
    CandidateUniverseEntryResponse,
    CandidateUniverseResponse,
    CandleResponse,
    CandleSeriesResponse,
    CollectionActivityBucketResponse,
    CollectionCoverageSegmentsResponse,
    CollectionDashboardTargetResponse,
    CollectionDataStatusResponse,
    CollectionPlanResponse,
    CollectionRunResponse,
    CollectionRunsResponse,
    CollectionTargetsResponse,
    CollectionWorkerErrorResponse,
    CollectionWorkerStatusResponse,
    CoverageSegmentResponse,
    CoverageStatusResponse,
    DashboardAuditLogSummaryResponse,
    DashboardCollectionActivityResponse,
    DashboardCoverageResponse,
    DashboardMissingRangesResponse,
    DashboardOperationsTrendResponse,
    DashboardOverviewResponse,
    DashboardRealtimeHeatmapResponse,
    DashboardStorageBreakdownResponse,
    DashboardSummaryResponse,
    DashboardTargetsResponse,
    DashboardTotalsResponse,
    HealthCheckResponse,
    InstrumentDetailResponse,
    InstrumentResponse,
    MarketListResponse,
    MarketListRowResponse,
    MetricPrincipleResponse,
    MissingRangeSummaryResponse,
    NotificationEventResponse,
    NotificationEventsResponse,
    OperationsTrendPointResponse,
    OrderbookSummariesResponse,
    OrderbookSummaryResponse,
    QualityHistoryEventResponse,
    RealtimeCollectionHeatmapCellResponse,
    RealtimeCollectionHeatmapRowResponse,
    RealtimeWorkerStatusResponse,
    StorageBreakdownItemResponse,
    TickerSnapshotResponse,
    TickerSnapshotsResponse,
)
from goodmoneying_shared.models import (
    BackfillJob,
    BackfillPlan,
    CandleView,
    CollectionDashboardTarget,
    CollectionWorkerStatusSummary,
    CoverageSegment,
    CoverageStatus,
    DashboardSummary,
    Instrument,
    MissingRangeSummary,
    NotificationEvent,
    OperationsTrendPoint,
    OrderbookSummary,
    RealtimeCollectionHeatmapRow,
    StorageBreakdownItem,
    TickerSnapshot,
    decimal_string,
)
from goodmoneying_shared.repository import OperationsRepository
from goodmoneying_shared.time import now_utc

CANDLE_UNITS = {"1m", "3m", "5m", "10m", "15m", "30m", "60m", "240m", "1d"}

METRIC_PRINCIPLES = [
    MetricPrincipleResponse(
        metricKey="rateLimitRemainingPercent",
        label="업비트 Rate Limit 여유율",
        displayStatus="excluded",
        evidenceStatus="missing_persistence",
        reason="실제 Upbit 응답 헤더가 영속화되지 않아 운영 콘솔에서 백분율로 표시하지 않는다.",
    ),
    MetricPrincipleResponse(
        metricKey="duplicateRows24h",
        label="중복 저장 시도",
        displayStatus="excluded",
        evidenceStatus="missing_measurement",
        reason=(
            "업서트 충돌 또는 중복 저장 시도 측정값이 없어 "
            "운영 콘솔에서 행 수로 표시하지 않는다."
        ),
    ),
]


class OperationsService:
    def __init__(
        self,
        repository: OperationsRepository,
        dashboard_refresh_seconds: Mapping[str, int] | None = None,
    ) -> None:
        self._repository = repository
        self._dashboard_refresh_seconds = DEFAULT_DASHBOARD_REFRESH_SECONDS.copy()
        if dashboard_refresh_seconds is not None:
            self._dashboard_refresh_seconds.update(dashboard_refresh_seconds)

    def dashboard_summary(self) -> DashboardSummaryResponse:
        return dashboard_to_response(self._repository.dashboard_summary())

    def dashboard_overview(self) -> DashboardOverviewResponse:
        summary = self.dashboard_summary()
        return DashboardOverviewResponse(
            status=summary.status,
            refreshedAt=summary.refreshedAt,
            recommendedRefreshSeconds=self._refresh_seconds("overview"),
            totals=summary.totals,
            alerts=summary.alerts,
            healthChecks=summary.healthChecks,
            metricPrinciples=summary.metricPrinciples,
        )

    def dashboard_targets(self, limit: int, offset: int) -> DashboardTargetsResponse:
        targets = [
            dashboard_target_to_response(target)
            for target in self._repository.collection_dashboard_targets()
        ]
        return DashboardTargetsResponse(
            items=targets[offset : offset + limit],
            total=len(targets),
            limit=limit,
            offset=offset,
            recommendedRefreshSeconds=self._refresh_seconds("targets"),
            refreshedAt=now_utc(),
        )

    def dashboard_coverage(self, limit: int, offset: int) -> DashboardCoverageResponse:
        coverage = [coverage_to_response(item) for item in self._repository.dashboard_coverage()]
        return DashboardCoverageResponse(
            items=coverage[offset : offset + limit],
            total=len(coverage),
            limit=limit,
            offset=offset,
            recommendedRefreshSeconds=self._refresh_seconds("coverage"),
            refreshedAt=now_utc(),
        )

    def dashboard_collection_activity(self) -> DashboardCollectionActivityResponse:
        return DashboardCollectionActivityResponse(
            items=[
                CollectionActivityBucketResponse(
                    bucketStartAt=bucket.bucket_start_at,
                    runCount=bucket.run_count,
                    resultCount=bucket.result_count,
                    status=bucket.status,
                )
                for bucket in self._repository.dashboard_collection_activity()
            ],
            recommendedRefreshSeconds=self._refresh_seconds("collectionActivity"),
            refreshedAt=now_utc(),
        )

    def dashboard_realtime_heatmap(
        self, limit: int, offset: int
    ) -> DashboardRealtimeHeatmapResponse:
        heatmap = [
            realtime_heatmap_row_to_response(row)
            for row in self._repository.dashboard_realtime_heatmap()
        ]
        return DashboardRealtimeHeatmapResponse(
            items=heatmap[offset : offset + limit],
            total=len(heatmap),
            limit=limit,
            offset=offset,
            recommendedRefreshSeconds=self._refresh_seconds("realtimeHeatmap"),
            refreshedAt=now_utc(),
        )

    def dashboard_storage_breakdown(self) -> DashboardStorageBreakdownResponse:
        return DashboardStorageBreakdownResponse(
            items=[
                storage_breakdown_to_response(item)
                for item in self._repository.dashboard_storage_breakdown()
            ],
            recommendedRefreshSeconds=self._refresh_seconds("storageBreakdown"),
            refreshedAt=now_utc(),
        )

    def dashboard_operations_trend(self) -> DashboardOperationsTrendResponse:
        return DashboardOperationsTrendResponse(
            items=[
                operations_trend_to_response(item)
                for item in self._repository.dashboard_operations_trend()
            ],
            recommendedRefreshSeconds=self._refresh_seconds("operationsTrend"),
            refreshedAt=now_utc(),
        )

    def dashboard_missing_ranges(
        self, limit: int, offset: int
    ) -> DashboardMissingRangesResponse:
        missing_ranges = [
            missing_range_to_response(item) for item in self._repository.dashboard_missing_ranges()
        ]
        return DashboardMissingRangesResponse(
            items=missing_ranges[offset : offset + limit],
            total=len(missing_ranges),
            limit=limit,
            offset=offset,
            recommendedRefreshSeconds=self._refresh_seconds("missingRanges"),
            refreshedAt=now_utc(),
        )

    def dashboard_audit_log_summary(self) -> DashboardAuditLogSummaryResponse:
        audit_log_summary = self._repository.dashboard_audit_log_summary()
        return DashboardAuditLogSummaryResponse(
            targetChangeCount24h=audit_log_summary.target_change_count_24h,
            backfillChangeCount24h=audit_log_summary.backfill_change_count_24h,
            latestChangeAt=audit_log_summary.latest_change_at,
            latestChangeLabel=audit_log_summary.latest_change_label,
            recommendedRefreshSeconds=self._refresh_seconds("auditLogSummary"),
            refreshedAt=now_utc(),
        )

    def _refresh_seconds(self, key: str) -> int:
        return self._dashboard_refresh_seconds[key]

    def candidate_universe(self) -> CandidateUniverseResponse:
        ranked_at, entries = self._repository.list_candidate_universe()
        dashboard_targets = self._repository.collection_dashboard_targets()
        targets_by_instrument_id = {target.instrument.id: target for target in dashboard_targets}
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
                    qualityStatus=candidate_quality_status(
                        targets_by_instrument_id.get(entry.instrument.id)
                    ),
                    qualityDetail=candidate_quality_detail(
                        targets_by_instrument_id.get(entry.instrument.id)
                    ),
                    collectionRangeDisplay=candidate_collection_range_display(
                        targets_by_instrument_id.get(entry.instrument.id)
                    ),
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

    def collection_coverage_segments(
        self, instrument_id: int
    ) -> CollectionCoverageSegmentsResponse:
        return CollectionCoverageSegmentsResponse(
            instrumentId=instrument_id,
            items=[
                coverage_segment_to_response(item)
                for item in self._repository.coverage_segments_for(instrument_id)
            ],
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
                    storageRowCount=row.storage_row_count,
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
        coverage = self._repository.coverage_for(instrument_id)
        price_change_amount_24h = calculate_price_change_amount(
            ticker.trade_price, ticker.change_rate
        )
        trade_volume_24h, trade_volume_change_rate_24h = self._trade_volume_24h_change(
            instrument_id
        )
        return InstrumentDetailResponse(
            instrument=instrument_to_response(instrument),
            latestTicker=ticker_to_response(ticker),
            latestOrderbook=orderbook_to_response(orderbook),
            coverage=[coverage_to_response(item) for item in coverage],
            priceChangeAmount24h=decimal_string(price_change_amount_24h) or "0",
            priceChangeRate24h=decimal_string(ticker.change_rate) or "0",
            tradeVolume24h=decimal_string(trade_volume_24h) or "0",
            tradeVolumeChangeRate24h=decimal_string(trade_volume_change_rate_24h) or "0",
            tickerFreshnessLabel=format_freshness_label(ticker.collected_at),
            orderbookFreshnessLabel=format_freshness_label(orderbook.collected_at),
            qualityHistory=quality_history_to_response(coverage),
        )

    def _trade_volume_24h_change(self, instrument_id: int) -> tuple[Decimal, Decimal]:
        end_at = now_utc()
        current_start_at = end_at - timedelta(hours=24)
        previous_start_at = end_at - timedelta(hours=48)
        current_volume = sum(
            (
                Decimal(str(item.volume))
                for item in self._repository.candles(
                    instrument_id, "1m", current_start_at, end_at
                )
            ),
            Decimal("0"),
        )
        previous_volume = sum(
            (
                Decimal(str(item.volume))
                for item in self._repository.candles(
                    instrument_id, "1m", previous_start_at, current_start_at
                )
            ),
            Decimal("0"),
        )
        if previous_volume == 0:
            return current_volume, Decimal("0")
        return current_volume, (current_volume - previous_volume) / previous_volume

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


def quality_history_to_response(
    coverage: list[CoverageStatus],
) -> list[QualityHistoryEventResponse]:
    labels = {
        "source_candle": "캔들",
        "ticker_snapshot": "현재가",
        "orderbook_summary": "호가",
    }
    return [
        QualityHistoryEventResponse(
            occurredAt=item.last_successful_at,
            status=quality_history_status(item.status),
            title=f"{labels[item.data_type]} 수집 {status_label_for(item.status)}",
            detail=(
                f"커버리지 {decimal_string(item.progress_percent) or '0'}%, "
                f"결측 {item.missing_segment_count}구간"
            ),
        )
        for item in sorted(coverage, key=lambda value: value.last_successful_at, reverse=True)
    ]


def quality_history_status(status: str) -> Literal["normal", "warning", "incident"]:
    if status == "incident":
        return "incident"
    if status == "warning":
        return "warning"
    return "normal"


def status_label_for(status: str) -> str:
    if status == "normal":
        return "정상"
    if status == "warning":
        return "주의"
    if status == "incident":
        return "장애"
    return "진행 중"


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
            storageRowsToday=item.storage_rows_today,
            realtimeRowsLastMinute=item.realtime_rows_last_minute,
            backfillRowsLastMinute=item.backfill_rows_last_minute,
            recentRequestCount=item.recent_request_count,
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
        metricPrinciples=METRIC_PRINCIPLES,
        collectionActivity=[
            CollectionActivityBucketResponse(
                bucketStartAt=bucket.bucket_start_at,
                runCount=bucket.run_count,
                resultCount=bucket.result_count,
                status=bucket.status,
            )
            for bucket in item.collection_activity
        ],
        realtimeCollectionHeatmap=[
            realtime_heatmap_row_to_response(heatmap_row)
            for heatmap_row in item.realtime_collection_heatmap
        ],
        storageBreakdown=[
            storage_breakdown_to_response(breakdown) for breakdown in item.storage_breakdown
        ],
        operationsTrend=[operations_trend_to_response(point) for point in item.operations_trend],
        missingRangeTop=[missing_range_to_response(summary) for summary in item.missing_range_top],
        auditLogSummary=AuditLogSummaryResponse(
            targetChangeCount24h=item.audit_log_summary.target_change_count_24h,
            backfillChangeCount24h=item.audit_log_summary.backfill_change_count_24h,
            latestChangeAt=item.audit_log_summary.latest_change_at,
            latestChangeLabel=item.audit_log_summary.latest_change_label,
        ),
        workerStatus=worker_status_to_response(item.worker_status),
    )


def worker_status_to_response(
    item: CollectionWorkerStatusSummary,
) -> CollectionWorkerStatusResponse:
    return CollectionWorkerStatusResponse(
        realtime=RealtimeWorkerStatusResponse(
            status=item.realtime.status,
            statusLabel=item.realtime.status_label,
            statusDetail=item.realtime.status_detail,
            lastHeartbeatAt=item.realtime.last_heartbeat_at,
            lastCollectedAt=item.realtime.last_collected_at,
            errorCount24h=item.realtime.error_count_24h,
            failureRate24h=decimal_string(item.realtime.failure_rate_24h) or "0",
            recentErrors=[
                CollectionWorkerErrorResponse(
                    occurredAt=error.occurred_at,
                    code=error.code,
                    message=error.message,
                )
                for error in item.realtime.recent_errors
            ],
        ),
        backfill=BackfillWorkerStatusResponse(
            status=item.backfill.status,
            statusLabel=item.backfill.status_label,
            statusDetail=item.backfill.status_detail,
            lastHeartbeatAt=item.backfill.last_heartbeat_at,
            lastCollectedAt=item.backfill.last_collected_at,
            totalErrorCount=item.backfill.total_error_count,
            failureRateAll=decimal_string(item.backfill.failure_rate_all) or "0",
            runningTargetCount=item.backfill.running_target_count,
            totalTargetCount=item.backfill.total_target_count,
            recentErrors=[
                CollectionWorkerErrorResponse(
                    occurredAt=error.occurred_at,
                    code=error.code,
                    message=error.message,
                )
                for error in item.backfill.recent_errors
            ],
        ),
    )


def realtime_heatmap_row_to_response(
    heatmap_row: RealtimeCollectionHeatmapRow,
) -> RealtimeCollectionHeatmapRowResponse:
    return RealtimeCollectionHeatmapRowResponse(
        instrument=instrument_to_response(heatmap_row.instrument),
        instrumentDisplayName=heatmap_row.instrument_display_name,
        hourlyBuckets=[
            RealtimeCollectionHeatmapCellResponse(
                bucketStartAt=bucket.bucket_start_at,
                expectedRowsAll=bucket.expected_rows_all,
                actualRowsAll=bucket.actual_rows_all,
                expectedRowsByType=bucket.expected_rows_by_type,
                actualRowsByType=bucket.actual_rows_by_type,
                actualRatioPercent=decimal_string(bucket.actual_ratio_percent) or "0",
                status=bucket.status,
            )
            for bucket in heatmap_row.hourly_buckets
        ],
    )


def storage_breakdown_to_response(item: StorageBreakdownItem) -> StorageBreakdownItemResponse:
    return StorageBreakdownItemResponse(
        dataType=item.data_type,
        label=item.label,
        rowCount=item.row_count,
        bytes=item.bytes,
        bytesDisplay=item.bytes_display,
        sharePercent=decimal_string(item.share_percent) or "0",
    )


def operations_trend_to_response(item: OperationsTrendPoint) -> OperationsTrendPointResponse:
    return OperationsTrendPointResponse(
        bucketDate=item.bucket_date,
        coveragePercent=decimal_string(item.coverage_percent) or "0",
        storageBytes=item.storage_bytes,
        warningTargets=item.warning_targets,
        incidentTargets=item.incident_targets,
    )


def missing_range_to_response(item: MissingRangeSummary) -> MissingRangeSummaryResponse:
    return MissingRangeSummaryResponse(
        instrument=instrument_to_response(item.instrument),
        missingSegmentCount=item.missing_segment_count,
        coveragePercent=decimal_string(item.coverage_percent) or "0",
        lastSuccessfulAt=item.last_successful_at,
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
            coverage_segment_to_response(segment) for segment in target.coverage_segments
        ],
        changeRate=decimal_string(target.change_rate) or "0",
        accTradePrice24hDisplay=target.acc_trade_price_24h_display,
        tickerFreshnessLabel=format_freshness_label(target.ticker_collected_at),
        coveragePercent=decimal_string(target.coverage_percent) or "0",
        storageRowCount=target.storage_row_count,
        storageBytesDisplay=target.storage_bytes_display,
    )


def coverage_segment_to_response(item: CoverageSegment) -> CoverageSegmentResponse:
    return CoverageSegmentResponse(
        dataType=item.data_type,
        status=item.status,
        offsetPercent=decimal_string(item.offset_percent) or "0",
        widthPercent=decimal_string(item.width_percent) or "0",
        segmentStartAt=item.segment_start_at,
        segmentEndAt=item.segment_end_at,
        label=item.label,
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


def calculate_price_change_amount(trade_price: Decimal, change_rate: Decimal) -> Decimal:
    denominator = Decimal("1") + Decimal(str(change_rate))
    if denominator == 0:
        return Decimal("0")
    previous_price = Decimal(str(trade_price)) / denominator
    return Decimal(str(trade_price)) - previous_price


def candidate_quality_status(
    target: CollectionDashboardTarget | None,
) -> Literal["normal", "warning", "incident"]:
    if target is None:
        return "warning"
    if target.overall_status in {"latest_collecting", "collecting"}:
        return "normal"
    if target.overall_status == "incident":
        return "incident"
    return "warning"


def candidate_quality_detail(target: CollectionDashboardTarget | None) -> str:
    if target is None:
        return "수집 계획 없음: 후보에는 포함됐지만 활성 수집 대상이 아니다."
    details = [
        f"{status.label} {status.status_label}, 결측 {status.missing_segment_count}구간, "
        f"진행률 {decimal_string(status.progress_percent) or '0'}%"
        for status in target.data_statuses
    ]
    return " / ".join(details)


def candidate_collection_range_display(target: CollectionDashboardTarget | None) -> str:
    if target is None:
        return "수집 계획 없음"
    return target.plan.display_range
