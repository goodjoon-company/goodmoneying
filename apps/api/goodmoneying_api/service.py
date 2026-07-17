from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
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
    CollectionWorkerDiagnosticResponse,
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
    IndicatorPointResponse,
    IndicatorSeriesResponse,
    InstrumentDetailResponse,
    InstrumentResponse,
    MarketListResponse,
    MarketListRowResponse,
    MarketStatisticResponse,
    MarketStatisticsResponse,
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
from goodmoneying_shared.indicator_store import (
    StoredIndicatorPoint,
    StoredMarketStatistic,
    indicator_projection_ceiling,
    market_statistic_projection_ceiling,
    materialize_indicator_points,
    materialize_market_statistics,
    read_indicator_points,
    read_market_statistics,
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
from goodmoneying_shared.time import now_kst
from goodmoneying_shared.versioned_indicators import (
    INDICATOR_DEFINITION_VERSIONS,
    IndicatorPoint,
    calculate_indicator_series,
)
from goodmoneying_shared.versioned_market_statistics import (
    CALCULATION_VERSION as MARKET_STATISTICS_VERSION,
)
from goodmoneying_shared.versioned_market_statistics import (
    calculate_market_statistics,
)

CANDLE_UNITS = {"1m", "3m", "5m", "10m", "15m", "30m", "1h", "4h", "1d", "1w", "1M"}
ANALYSIS_UNITS = {
    "1m": "1m",
    "3m": "3m",
    "5m": "5m",
    "10m": "10m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
    "1w": "1w",
    "1M": "1M",
}
ANALYSIS_RANGE_DAYS = {1, 7, 30, 90, 365, 1095}


class AnalysisSubscriptionError(ValueError):
    def __init__(
        self,
        code: Literal["INVALID_MESSAGE", "NOT_WATCHLISTED", "NOT_FOUND"],
        message: str,
    ) -> None:
        super().__init__(message)
        self.code = code


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
            "업서트 충돌 또는 중복 저장 시도 측정값이 없어 운영 콘솔에서 행 수로 표시하지 않는다."
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

    def dashboard_stream_interval_seconds(self) -> int:
        return self._refresh_seconds("realtimeHeatmap")

    def market_list_stream_interval_seconds(self) -> int:
        return self._refresh_seconds("realtimeHeatmap")

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
            refreshedAt=now_kst(),
        )

    def dashboard_coverage(self, limit: int, offset: int) -> DashboardCoverageResponse:
        coverage = [coverage_to_response(item) for item in self._repository.dashboard_coverage()]
        return DashboardCoverageResponse(
            items=coverage[offset : offset + limit],
            total=len(coverage),
            limit=limit,
            offset=offset,
            recommendedRefreshSeconds=self._refresh_seconds("coverage"),
            refreshedAt=now_kst(),
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
            refreshedAt=now_kst(),
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
            refreshedAt=now_kst(),
        )

    def dashboard_storage_breakdown(self) -> DashboardStorageBreakdownResponse:
        return DashboardStorageBreakdownResponse(
            items=[
                storage_breakdown_to_response(item)
                for item in self._repository.dashboard_storage_breakdown()
            ],
            recommendedRefreshSeconds=self._refresh_seconds("storageBreakdown"),
            refreshedAt=now_kst(),
        )

    def dashboard_operations_trend(self) -> DashboardOperationsTrendResponse:
        return DashboardOperationsTrendResponse(
            items=[
                operations_trend_to_response(item)
                for item in self._repository.dashboard_operations_trend()
            ],
            recommendedRefreshSeconds=self._refresh_seconds("operationsTrend"),
            refreshedAt=now_kst(),
        )

    def dashboard_missing_ranges(self, limit: int, offset: int) -> DashboardMissingRangesResponse:
        missing_ranges = [
            missing_range_to_response(item) for item in self._repository.dashboard_missing_ranges()
        ]
        return DashboardMissingRangesResponse(
            items=missing_ranges[offset : offset + limit],
            total=len(missing_ranges),
            limit=limit,
            offset=offset,
            recommendedRefreshSeconds=self._refresh_seconds("missingRanges"),
            refreshedAt=now_kst(),
        )

    def dashboard_audit_log_summary(self) -> DashboardAuditLogSummaryResponse:
        audit_log_summary = self._repository.dashboard_audit_log_summary()
        return DashboardAuditLogSummaryResponse(
            targetChangeCount24h=audit_log_summary.target_change_count_24h,
            backfillChangeCount24h=audit_log_summary.backfill_change_count_24h,
            latestChangeAt=audit_log_summary.latest_change_at,
            latestChangeLabel=audit_log_summary.latest_change_label,
            recommendedRefreshSeconds=self._refresh_seconds("auditLogSummary"),
            refreshedAt=now_kst(),
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
                    favoriteOrder=entry.favorite_order,
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
                    collectedStartAt=candidate_collected_start_at(
                        targets_by_instrument_id.get(entry.instrument.id)
                    ),
                    collectedEndAt=candidate_collected_end_at(
                        targets_by_instrument_id.get(entry.instrument.id)
                    ),
                    isRealtimeTarget=candidate_is_realtime_target(
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
                    assetType=row.asset_type,
                    isFavorite=row.is_favorite,
                    favoriteOrder=row.favorite_order,
                    tradePrice=decimal_string(row.trade_price),
                    priceCurrency=row.price_currency,
                    accTradePrice24h=decimal_string(row.acc_trade_price_24h) or "0",
                    accTradePrice24hDisplay=format_krw(row.acc_trade_price_24h),
                    tradeAmountCurrency=row.trade_amount_currency,
                    changeRate=decimal_string(row.change_rate),
                    changeRateBasis=row.change_rate_basis,
                    tickerCollectedAt=row.ticker_collected_at,
                    orderbookCollectedAt=row.orderbook_collected_at,
                    qualityStatus=row.quality_status,
                    coveragePercent=decimal_string(row.coverage_percent) or "0",
                    candleCoverageStartAt=row.candle_coverage_start_at,
                    candleCoverageEndAt=row.candle_coverage_end_at,
                    candleCoverageCurrentAt=row.candle_coverage_current_at,
                    oneMinuteCandleCount=row.one_minute_candle_count,
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
        end_at = now_kst()
        current_start_at = end_at - timedelta(hours=24)
        previous_start_at = end_at - timedelta(hours=48)
        current_volume = sum(
            (
                Decimal(str(item.volume))
                for item in self._repository.candles(instrument_id, "1m", current_start_at, end_at)
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
        self,
        instrument_id: int,
        unit: str,
        start_at: datetime,
        end_at: datetime,
        *,
        page_size: int = 500,
        cursor: datetime | None = None,
    ) -> CandleSeriesResponse:
        if unit not in CANDLE_UNITS:
            raise ValueError("지원하지 않는 캔들 단위다.")
        if any(value.tzinfo is None or value.utcoffset() is None for value in (start_at, end_at)):
            raise ValueError("캔들 조회 시각은 UTC 오프셋을 포함해야 한다.")
        if cursor is not None and (cursor.tzinfo is None or cursor.utcoffset() is None):
            raise ValueError("캔들 커서는 UTC 오프셋을 포함해야 한다.")
        if start_at >= end_at:
            raise ValueError("캔들 조회 종료 시각은 시작 시각보다 뒤여야 한다.")
        if end_at - start_at > timedelta(days=1095):
            raise ValueError("캔들 조회 범위는 최대 1095일이다.")
        if not 1 <= page_size <= 500:
            raise ValueError("페이지 크기는 1에서 500 사이여야 한다.")
        start_at = start_at.astimezone(UTC)
        end_at = end_at.astimezone(UTC)
        cursor = cursor.astimezone(UTC) if cursor else None
        page_reader = getattr(self._repository, "candle_page", None)
        if callable(page_reader):
            page, next_cursor = page_reader(
                instrument_id, unit, start_at, end_at, page_size, cursor
            )
        else:
            rows = self._repository.candles(instrument_id, unit, start_at, end_at)
            if cursor is not None:
                rows = [item for item in rows if item.started_at > cursor]
            page = rows[:page_size]
            next_cursor = page[-1].started_at if len(rows) > page_size and page else None
        return CandleSeriesResponse(
            unit=unit,
            candles=[candle_to_response(item) for item in page],
            nextCursor=next_cursor.astimezone(UTC).isoformat().replace("+00:00", "Z")
            if next_cursor
            else None,
        )

    def analysis_snapshot(
        self, instrument_id: int, unit: str, range_days: int
    ) -> dict[str, object]:
        repository_unit = ANALYSIS_UNITS.get(unit)
        if repository_unit is None or range_days not in ANALYSIS_RANGE_DAYS:
            raise AnalysisSubscriptionError(
                "INVALID_MESSAGE", "지원하지 않는 차트 시간 단위 또는 기간입니다."
            )
        active_ids = {instrument.id for instrument in self._repository.list_active_targets()}
        if instrument_id not in active_ids:
            raise AnalysisSubscriptionError(
                "NOT_WATCHLISTED", "관심목록에 있는 코인만 분석할 수 있습니다."
            )
        instrument = self._repository.get_instrument(instrument_id)
        ticker = self._repository.latest_ticker(instrument_id)
        orderbook = self._repository.latest_orderbook(instrument_id)
        if instrument is None or ticker is None or orderbook is None:
            raise AnalysisSubscriptionError("NOT_FOUND", "분석할 시장 데이터가 없습니다.")
        end_at = now_kst()
        range_start = end_at - timedelta(days=range_days)
        candles = self._indicator_source_candles(
            instrument_id, repository_unit, end_at, knowledge_at=end_at
        )
        candles = [
            item for item in candles if range_start <= item.started_at < end_at
        ]
        if repository_unit in {"1m", "3m", "5m", "10m", "15m", "30m", "1h", "4h"}:
            candles = candles[-1000:]
        responses = [candle_to_response(item) for item in candles]
        indicator_start = candles[0].started_at if candles else range_start
        indicator_series = self.indicator_points(
            instrument_id,
            repository_unit,
            indicator_start,
            end_at,
            as_of=end_at,
            page_size=500,
            cursor=None,
            definition_version=None,
        )
        stored_points = list(indicator_series.items)
        while indicator_series.nextCursor is not None:
            indicator_series = self.indicator_points(
                instrument_id,
                repository_unit,
                indicator_start,
                end_at,
                as_of=end_at,
                page_size=500,
                cursor=indicator_series.nextCursor,
                definition_version=None,
            )
            stored_points.extend(indicator_series.items)
        points_by_started_at = {item.startedAt.astimezone(UTC): item for item in stored_points}
        stored_points = [
            points_by_started_at[item.started_at.astimezone(UTC)]
            for item in candles
            if item.started_at.astimezone(UTC) in points_by_started_at
        ]
        trade = self._repository.trade_summary(instrument_id, end_at - timedelta(minutes=1), end_at)
        return {
            "instrument": instrument_to_response(instrument).model_dump(mode="json"),
            "unit": unit,
            "candles": [item.model_dump(mode="json") for item in responses],
            "indicatorPoints": [_indicator_point_legacy(item) for item in stored_points],
            "market": {
                "ticker": ticker_to_response(ticker).model_dump(mode="json"),
                "orderbook": orderbook_to_response(orderbook).model_dump(mode="json"),
                "tradeSummary": {
                    "tradeCount": trade.trade_count,
                    "buyVolume": decimal_string(trade.buy_volume) or "0",
                    "sellVolume": decimal_string(trade.sell_volume) or "0",
                    "lastTradeAt": trade.last_trade_at.isoformat() if trade.last_trade_at else None,
                },
            },
        }

    def indicator_points(
        self,
        instrument_id: int,
        unit: str,
        start_at: datetime,
        end_at: datetime,
        *,
        as_of: datetime,
        page_size: int,
        cursor: str | None,
        definition_version: str | None,
    ) -> IndicatorSeriesResponse:
        _validate_indicator_query(unit, start_at, end_at, as_of, page_size)
        hashes = {item.definition_hash for item in INDICATOR_DEFINITION_VERSIONS.values()}
        definition_set_hash = sha256("|".join(sorted(hashes)).encode()).hexdigest()
        if definition_version is not None and definition_version != definition_set_hash:
            raise ValueError("요청한 지표 정의 집합 해시가 현재 물질화와 다르다.")
        decoded = (
            _decode_indicator_cursor(
                cursor, as_of, definition_set_hash, instrument_id, unit, start_at, end_at
            )
            if cursor
            else None
        )
        cursor_at = decoded[0] if decoded else None
        ceiling_id = decoded[1] if decoded else None
        if callable(getattr(self._repository, "_connect", None)):
            if ceiling_id is None:
                ceiling_id = indicator_projection_ceiling(
                    self._repository, instrument_id, unit, as_of, definition_set_hash
                )
            stored = list(
                read_indicator_points(
                    self._repository,
                    instrument_id,
                    unit,
                    start_at.astimezone(UTC),
                    end_at.astimezone(UTC),
                    as_of.astimezone(UTC),
                    definition_set_hash,
                    after_at=cursor_at,
                    ceiling_id=ceiling_id,
                    limit=page_size + 1,
                )
            )
        else:
            source = self._indicator_source_candles(
                instrument_id, unit, end_at.astimezone(UTC), knowledge_at=as_of.astimezone(UTC)
            )
            calculated = calculate_indicator_series(source, unit=unit)
            stored = list(
                materialize_indicator_points(
                    self._repository, instrument_id, unit, calculated, source, definition_set_hash
                )
            )
            if ceiling_id is None:
                ceiling_id = indicator_projection_ceiling(
                    self._repository, instrument_id, unit, as_of, definition_set_hash
                )
            stored = [item for item in stored if item.materialization_id <= ceiling_id]
        stored = [
            item
            for item in stored
            if start_at.astimezone(UTC) <= item.point.started_at < end_at.astimezone(UTC)
        ]
        if cursor_at is not None:
            stored = [item for item in stored if item.point.started_at > cursor_at]
        page = stored[:page_size]
        next_item = page[-1] if len(stored) > page_size and page else None
        return IndicatorSeriesResponse(
            unit=unit,
            asOf=as_of.astimezone(UTC),
            definitionSetHash=definition_set_hash,
            items=[_indicator_point_response(item.point, item.materialization_id) for item in page],
            nextCursor=(
                _encode_indicator_cursor(
                    next_item,
                    as_of,
                    definition_set_hash,
                    ceiling_id,
                    instrument_id,
                    unit,
                    start_at,
                    end_at,
                )
                if next_item
                else None
            ),
        )

    def market_statistics(
        self,
        instrument_id: int,
        unit: str,
        start_at: datetime,
        end_at: datetime,
        *,
        as_of: datetime,
        page_size: int,
        cursor: str | None,
        calculation_version: str | None,
    ) -> MarketStatisticsResponse:
        _validate_indicator_query(unit, start_at, end_at, as_of, page_size)
        if calculation_version not in {None, MARKET_STATISTICS_VERSION}:
            raise ValueError("알 수 없는 시장 통계 계산 버전이다.")
        version_hash = sha256(MARKET_STATISTICS_VERSION.encode()).hexdigest()
        decoded = (
            _decode_indicator_cursor(
                cursor, as_of, version_hash, instrument_id, unit, start_at, end_at
            )
            if cursor
            else None
        )
        cursor_at = decoded[0] if decoded else None
        ceiling_id = decoded[1] if decoded else None
        if callable(getattr(self._repository, "_connect", None)):
            if ceiling_id is None:
                ceiling_id = market_statistic_projection_ceiling(
                    self._repository, instrument_id, unit, as_of
                )
            stored = list(
                read_market_statistics(
                    self._repository,
                    instrument_id,
                    unit,
                    start_at.astimezone(UTC),
                    end_at.astimezone(UTC),
                    as_of.astimezone(UTC),
                    after_at=cursor_at,
                    ceiling_id=ceiling_id,
                    limit=page_size + 1,
                )
            )
        else:
            source = self._indicator_source_candles(
                instrument_id, unit, end_at.astimezone(UTC), knowledge_at=as_of.astimezone(UTC)
            )
            calculated = calculate_market_statistics(source, unit)
            stored = list(
                materialize_market_statistics(self._repository, instrument_id, unit, calculated)
            )
            if ceiling_id is None:
                ceiling_id = market_statistic_projection_ceiling(
                    self._repository, instrument_id, unit, as_of
                )
            stored = [item for item in stored if item.materialization_id <= ceiling_id]
        stored = [
            item
            for item in stored
            if start_at.astimezone(UTC) <= item.point.started_at < end_at.astimezone(UTC)
        ]
        if cursor_at is not None:
            stored = [item for item in stored if item.point.started_at > cursor_at]
        page = stored[:page_size]
        next_item = page[-1] if len(stored) > page_size and page else None
        return MarketStatisticsResponse(
            unit=unit,
            asOf=as_of.astimezone(UTC),
            items=[
                MarketStatisticResponse(
                    startedAt=item.point.started_at,
                    calculationVersion=MARKET_STATISTICS_VERSION,
                    closeReturn1=decimal_string(item.point.close_return_1),
                    realizedVolatility20=decimal_string(item.point.realized_volatility_20),
                    tradeVolume=decimal_string(item.point.trade_volume),
                    tradeAmount=decimal_string(item.point.trade_amount),
                    volatilitySampleCount=item.point.volatility_sample_count,
                    inputCompletenessRatio=decimal_string(item.point.input_completeness_ratio)
                    or "0",
                    returnStatus=item.point.return_status,
                    volatilityStatus=item.point.volatility_status,
                    tradeStatus=item.point.trade_status,
                    materializationId=item.materialization_id,
                    sourceRevisionThroughId=item.point.source_revision_through_id,
                    qualityEventThroughId=item.point.quality_event_through_id,
                    sourceAsOf=item.point.source_as_of or item.point.started_at,
                    knowledgeAt=item.point.knowledge_at or item.point.started_at,
                    contentHash=item.point.content_hash,
                )
                for item in page
            ],
            nextCursor=(
                _encode_market_statistic_cursor(
                    next_item,
                    as_of,
                    version_hash,
                    ceiling_id,
                    instrument_id,
                    unit,
                    start_at,
                    end_at,
                )
                if next_item
                else None
            ),
        )

    def _indicator_source_candles(
        self, instrument_id: int, unit: str, end_at: datetime, *, knowledge_at: datetime
    ) -> list[CandleView]:
        beginning = datetime(1970, 1, 1, tzinfo=UTC)
        if unit != "1m":
            rows = self._repository.candle_rollups(
                instrument_id,
                unit,
                beginning,
                end_at,
                knowledge_at=knowledge_at,
            )
            if rows:
                return rows
        rows = self._repository.candles(instrument_id, unit, beginning, end_at)
        return [
            item
            for item in rows
            if (item.knowledge_at or item.source_as_of or item.started_at) <= knowledge_at
        ]

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

    def create_backfill_job(
        self,
        data_type: str,
        target_start_at: datetime,
        target_end_at: datetime,
        instrument_ids: list[int],
    ) -> BackfillJobResponse:
        plan = self._repository.create_backfill_plan(
            data_type,
            target_start_at,
            target_end_at,
            instrument_ids,
        )
        return backfill_job_to_response(self._repository.approve_backfill_job(plan.plan_id))

    def control_backfill_job(self, job_id: int, action: str) -> BackfillJobResponse:
        return backfill_job_to_response(self._repository.control_backfill_job(job_id, action))

    def delete_backfill_job(self, job_id: int) -> None:
        self._repository.delete_backfill_job(job_id)

    def backfill_jobs(self) -> list[BackfillJobResponse]:
        return [backfill_job_to_response(item) for item in self._repository.backfill_jobs()]

    def system_management_snapshot(self) -> dict[str, object]:
        """시스템 관리 화면에 필요한 작은 상태 조각을 조합한다."""
        dashboard = self.dashboard_summary()
        active_targets = self._repository.list_active_targets()
        active_by_id = {item.id: item for item in active_targets}
        backfill_items: list[dict[str, object]] = []
        for job in self._repository.backfill_jobs():
            if job.status not in {"pending", "leased", "running", "retry_wait", "paused"}:
                continue
            items = [job.current_target] if job.current_target else job.targets
            for item in items:
                if item is not None:
                    backfill_items.append(
                        {
                            "instrument": instrument_to_response(item).model_dump(mode="json"),
                            "dataTypes": [job.data_type],
                            "status": job.status,
                        }
                    )
        aggregation_job = self._repository.latest_candle_aggregation_job()
        incremental_job = self._repository.latest_candle_rollup_recompute_job()
        aggregation_worker = self._repository.collection_worker_runtime_status("candle_aggregation")
        aggregation_items: list[dict[str, object]] = []
        aggregation: dict[str, object] | None = None
        if aggregation_job is not None:
            for target in self._repository.candle_aggregation_job_targets(aggregation_job.id):
                instrument = active_by_id.get(target.instrument_id)
                if instrument is not None:
                    aggregation_items.append(
                        {
                            "instrument": instrument_to_response(instrument).model_dump(
                                mode="json"
                            ),
                            "unit": target.candle_unit,
                            "status": target.status,
                            "rowsWritten": target.rows_written,
                        }
                    )
            aggregation = {
                "id": aggregation_job.id,
                "status": aggregation_job.status,
                "progressPercent": str(aggregation_job.progress_percent),
                "totalTargetCount": aggregation_job.total_target_count,
                "completedTargetCount": aggregation_job.completed_target_count,
                "runningTargetCount": aggregation_job.running_target_count,
                "pendingTargetCount": aggregation_job.pending_target_count,
                "failedTargetCount": aggregation_job.failed_target_count,
                "items": aggregation_items,
            }
        return {
            "refreshedAt": now_kst().isoformat(),
            "realtime": {
                "status": dashboard.workerStatus.realtime.status,
                "statusLabel": dashboard.workerStatus.realtime.statusLabel,
                "items": [
                    {
                        "instrument": instrument_to_response(item).model_dump(mode="json"),
                        "dataTypes": ["source_candle", "ticker_snapshot", "orderbook_summary"],
                    }
                    for item in active_targets
                ]
                if dashboard.workerStatus.realtime.status == "running"
                else [],
            },
            "backfill": {
                "status": dashboard.workerStatus.backfill.status,
                "statusLabel": dashboard.workerStatus.backfill.statusLabel,
                "items": backfill_items,
            },
            "aggregationWorker": {
                "status": aggregation_worker.status,
                "statusLabel": aggregation_worker.status_label,
                "statusDetail": aggregation_worker.status_detail,
                "lastHeartbeatAt": (
                    aggregation_worker.last_heartbeat_at.isoformat()
                    if aggregation_worker.last_heartbeat_at
                    else None
                ),
            },
            "aggregation": aggregation,
            "incrementalAggregation": (
                {
                    "id": incremental_job.id,
                    "status": incremental_job.status,
                    "instrumentId": incremental_job.instrument_id,
                    "unit": incremental_job.candle_unit,
                    "rangeStartAt": incremental_job.range_start_at.isoformat(),
                    "rangeEndAt": incremental_job.range_end_at.isoformat(),
                    "attemptCount": incremental_job.attempt_count,
                    "maxAttempts": incremental_job.max_attempts,
                    "rowsWritten": incremental_job.rows_written,
                    "lastErrorCode": incremental_job.last_error_code,
                }
                if incremental_job is not None
                else None
            ),
        }

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
            collectedRowCount24h=item.realtime.collected_row_count_24h,
            errorCount24h=item.realtime.error_count_24h,
            failureRate24h=decimal_string(item.realtime.failure_rate_24h) or "0",
            diagnostics=[
                CollectionWorkerDiagnosticResponse(
                    label=diagnostic.label,
                    value=diagnostic.value,
                    detail=diagnostic.detail,
                )
                for diagnostic in item.realtime.diagnostics
            ],
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
            queuedJobCount=item.backfill.queued_job_count,
            queuedTargetCount=item.backfill.queued_target_count,
            diagnostics=[
                CollectionWorkerDiagnosticResponse(
                    label=diagnostic.label,
                    value=diagnostic.value,
                    detail=diagnostic.detail,
                )
                for diagnostic in item.backfill.diagnostics
            ],
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
                tradeCount=bucket.trade_count,
                averageTradesPerMinute=decimal_string(bucket.average_trades_per_minute) or "0",
                tradeStrength=decimal_string(bucket.trade_strength) or "0",
                tradeVolume=decimal_string(bucket.trade_volume) or "0",
                tradeAmount=decimal_string(bucket.trade_amount) or "0",
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
        startedAt=item.started_at.astimezone(UTC),
        open=decimal_string(item.open) or "0",
        high=decimal_string(item.high) or "0",
        low=decimal_string(item.low) or "0",
        close=decimal_string(item.close) or "0",
        volume=decimal_string(item.volume) or "0",
        tradeAmount=decimal_string(item.trade_amount) or "0",
        calculationVersion=item.calculation_version,
        sourceAsOf=(item.source_as_of or item.started_at).astimezone(UTC),
        knowledgeAt=(item.knowledge_at or item.source_as_of or item.started_at).astimezone(UTC),
        inputContentHash=item.input_content_hash,
        quality=item.quality,
        completeness=item.completeness,
    )


def _indicator_point_response(
    item: IndicatorPoint, materialization_id: int
) -> IndicatorPointResponse:
    knowledge_at = item.knowledge_at or item.started_at
    source_as_of = item.source_as_of or knowledge_at
    return IndicatorPointResponse(
        startedAt=item.started_at.astimezone(UTC),
        values={key: decimal_string(value) for key, value in item.values.items()},
        statuses=dict(item.statuses),
        definitionVersions=dict(item.definition_version_hashes),
        materializationId=materialization_id,
        sourceRevisionThroughId=item.source_revision_through_id,
        qualityEventThroughId=item.quality_event_through_id,
        knowledgeAt=knowledge_at.astimezone(UTC),
        sourceAsOf=source_as_of.astimezone(UTC),
    )


def _indicator_point_legacy(item: IndicatorPointResponse) -> dict[str, object]:
    return {
        "startedAt": item.startedAt.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        **item.values,
        "calculationStatus": item.statuses,
        "definitionVersions": item.definitionVersions,
        "materializationId": item.materializationId,
        "sourceRevisionThroughId": item.sourceRevisionThroughId,
        "qualityEventThroughId": item.qualityEventThroughId,
        "knowledgeAt": item.knowledgeAt.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "sourceAsOf": item.sourceAsOf.astimezone(UTC).isoformat().replace("+00:00", "Z"),
    }


def _validate_indicator_query(
    unit: str,
    start_at: datetime,
    end_at: datetime,
    as_of: datetime,
    page_size: int,
) -> None:
    if unit not in CANDLE_UNITS:
        raise ValueError("지원하지 않는 지표 캔들 단위다.")
    values = [start_at, end_at, as_of]
    if any(value.tzinfo is None or value.utcoffset() is None for value in values):
        raise ValueError("지표 조회 시각은 UTC 오프셋을 포함해야 한다.")
    if start_at >= end_at:
        raise ValueError("지표 조회는 UTC 반개방 구간 [from,to)이어야 한다.")
    if as_of < start_at:
        raise ValueError("asOf는 조회 시작 시각보다 빠를 수 없다.")
    if not 1 <= page_size <= 500:
        raise ValueError("페이지 크기는 1에서 500 사이여야 한다.")


def _encode_indicator_cursor(
    item: StoredIndicatorPoint,
    as_of: datetime,
    definition_set_hash: str,
    snapshot_ceiling_id: int,
    instrument_id: int,
    unit: str,
    start_at: datetime,
    end_at: datetime,
) -> str:
    payload = json.dumps(
        {
            "asOf": as_of.astimezone(UTC).isoformat(),
            "definitionSetHash": definition_set_hash,
            "instrumentId": instrument_id,
            "unit": unit,
            "from": start_at.astimezone(UTC).isoformat(),
            "to": end_at.astimezone(UTC).isoformat(),
            "materializationId": item.materialization_id,
            "snapshotCeilingId": snapshot_ceiling_id,
            "startedAt": item.point.started_at.astimezone(UTC).isoformat(),
            "sourceRevisionThroughId": item.point.source_revision_through_id,
            "qualityEventThroughId": item.point.quality_event_through_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _encode_market_statistic_cursor(
    item: StoredMarketStatistic,
    as_of: datetime,
    calculation_version_hash: str,
    snapshot_ceiling_id: int,
    instrument_id: int,
    unit: str,
    start_at: datetime,
    end_at: datetime,
) -> str:
    payload = json.dumps(
        {
            "asOf": as_of.astimezone(UTC).isoformat(),
            "definitionSetHash": calculation_version_hash,
            "instrumentId": instrument_id,
            "unit": unit,
            "from": start_at.astimezone(UTC).isoformat(),
            "to": end_at.astimezone(UTC).isoformat(),
            "materializationId": item.materialization_id,
            "snapshotCeilingId": snapshot_ceiling_id,
            "startedAt": item.point.started_at.astimezone(UTC).isoformat(),
            "sourceRevisionThroughId": item.point.source_revision_through_id,
            "qualityEventThroughId": item.point.quality_event_through_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_indicator_cursor(
    cursor: str,
    as_of: datetime,
    definition_set_hash: str,
    instrument_id: int,
    unit: str,
    start_at: datetime,
    end_at: datetime,
) -> tuple[datetime, int]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        if payload["asOf"] != as_of.astimezone(UTC).isoformat():
            raise ValueError("cursor의 asOf frontier가 현재 요청과 다르다.")
        if payload["definitionSetHash"] != definition_set_hash:
            raise ValueError("cursor의 지표 정의 버전이 현재 요청과 다르다.")
        expected_context = {
            "instrumentId": instrument_id,
            "unit": unit,
            "from": start_at.astimezone(UTC).isoformat(),
            "to": end_at.astimezone(UTC).isoformat(),
        }
        if any(payload[key] != value for key, value in expected_context.items()):
            raise ValueError("cursor의 조회 문맥이 현재 요청과 다르다.")
        started_at = datetime.fromisoformat(str(payload["startedAt"]))
        if started_at.tzinfo is None:
            raise ValueError
        ceiling_id = int(payload["snapshotCeilingId"])
        if ceiling_id < int(payload["materializationId"]):
            raise ValueError
        return started_at.astimezone(UTC), ceiling_id
    except (KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("cursor"):
            raise
        raise ValueError("유효하지 않은 지표 cursor다.") from exc


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
        estimatedRequestCount=item.estimated_request_count,
        totalTargetCount=item.total_target_count,
        completedTargetCount=item.completed_target_count,
        runningTargetIndex=item.running_target_index,
        currentTarget=(
            instrument_to_response(item.current_target) if item.current_target is not None else None
        ),
        currentTargetBackfillRowCount=item.current_target_backfill_row_count,
        processedMissingRangeCount=item.processed_missing_range_count,
        estimatedMissingRangeCount=item.estimated_missing_range_count,
        targetStartAt=item.target_start_at,
        targetEndAt=item.target_end_at,
        targets=[instrument_to_response(target) for target in item.targets],
        createdAt=item.created_at,
        attemptCount=item.attempt_count,
        maxAttempts=item.max_attempts,
        nextRetryAt=item.next_retry_at,
        lastErrorCode=item.last_error_code,
        deadLetterReason=item.dead_letter_reason,
    )


def format_freshness_label(value: datetime) -> str:
    age = now_kst() - value
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


def candidate_collected_start_at(target: CollectionDashboardTarget | None) -> datetime | None:
    if target is None:
        return None
    return target.collected_start_at


def candidate_collected_end_at(target: CollectionDashboardTarget | None) -> datetime | None:
    if target is None:
        return None
    return target.collected_end_at


def candidate_is_realtime_target(target: CollectionDashboardTarget | None) -> bool:
    return bool(target and target.plan.is_continuous)
