from __future__ import annotations

import builtins
import os
import time
from asyncio import wait_for
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from typing import Annotated, Protocol, cast
from uuid import uuid4

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse

from goodmoneying_api.dashboard_refresh import load_dashboard_refresh_seconds
from goodmoneying_api.dependencies import verify_operator_token
from goodmoneying_api.schemas import (
    BackfillJobResponse,
    BackfillJobsResponse,
    BackfillPlanResponse,
    CandidateUniverseResponse,
    CandleSeriesResponse,
    CollectionCoverageSegmentsResponse,
    CollectionRunsResponse,
    CollectionTargetsResponse,
    CoverageCountsResponse,
    CreateBackfillJobRequest,
    CreateBackfillPlanRequest,
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
    DataFoundationMarketResponse,
    DataFoundationResponse,
    DataFoundationSummaryResponse,
    HealthResponse,
    InstrumentDetailResponse,
    MarketCollectionPolicyResponse,
    MarketListResponse,
    NotificationEventsResponse,
    OrderbookSummariesResponse,
    TickerSnapshotsResponse,
    UpdateCollectionTargetsRequest,
    UpdateMarketTargetStateRequest,
    UpdateMarketTargetStateResponse,
)
from goodmoneying_api.service import AnalysisSubscriptionError, OperationsService
from goodmoneying_shared.data_foundation import (
    CoverageState,
    DataFoundationOverview,
    MarketCollectionPolicySettings,
)
from goodmoneying_shared.data_foundation_repository import (
    IdempotencyConflictError,
    PostgresDataFoundationRepository,
)
from goodmoneying_shared.postgres_repository import PostgresOperationsRepository
from goodmoneying_shared.repository import OperationsRepository
from goodmoneying_shared.sqlite_repository import SQLiteOperationsRepository
from goodmoneying_shared.time import now_kst


def create_repository_from_environment() -> OperationsRepository:
    database_url = os.getenv("GOODMONEYING_DATABASE_URL")
    runtime_mode = os.getenv("GOODMONEYING_RUNTIME_MODE")
    if runtime_mode not in {"development", "test", "production"}:
        raise RuntimeError(
            "GOODMONEYING_RUNTIME_MODE는 development, test, production 중 하나로 명시해야 한다."
        )
    if os.getenv("GOODMONEYING_DEMO_DATA") == "1":
        raise RuntimeError(
            "fixture demo repository는 더 이상 런타임에서 사용할 수 없다. "
            "E2E는 test-only HTTP mock helper 또는 명시적으로 주입한 테스트 저장소를 사용해야 한다."
        )
    if database_url and database_url.startswith(("postgres://", "postgresql://")):
        repository = PostgresOperationsRepository(database_url)
        if runtime_mode == "production":
            with repository._connect() as connection:
                connection.execute("SELECT 1")
        return repository
    if runtime_mode == "production":
        raise RuntimeError(
            "운영 모드는 연결 가능한 PostgreSQL GOODMONEYING_DATABASE_URL을 필요로 한다."
        )
    return SQLiteOperationsRepository()


class DataFoundationApiRepository(Protocol):
    def overview(self) -> DataFoundationOverview: ...

    def set_market_target_state(
        self,
        market_code: str,
        *,
        state: str,
        actor: str,
        reason: str,
        changed_at: datetime,
        request_id: str,
        idempotency_key: str,
        requested_at: datetime,
        policy: MarketCollectionPolicySettings | None = None,
    ) -> datetime: ...


class EmptyDataFoundationRepository:
    def overview(self) -> DataFoundationOverview:
        from goodmoneying_shared.data_foundation import DEFAULT_KRW_START_AT

        return DataFoundationOverview(
            market_count=0,
            krw_market_count=0,
            active_target_count=0,
            pending_backfill_job_count=0,
            desired_subscription_count=0,
            policy_start_at=DEFAULT_KRW_START_AT,
            coverage_counts={
                "available": 0,
                "no_trade": 0,
                "missing": 0,
                "unavailable": 0,
                "unverified": 0,
            },
            markets=[],
        )

    def set_market_target_state(
        self,
        market_code: str,
        *,
        state: str,
        actor: str,
        reason: str,
        changed_at: datetime,
        request_id: str,
        idempotency_key: str,
        requested_at: datetime,
        policy: MarketCollectionPolicySettings | None = None,
    ) -> datetime:
        raise ValueError("변경할 시장을 찾을 수 없다.")


def create_app(
    repository: OperationsRepository | None = None,
    *,
    data_foundation_repository: DataFoundationApiRepository | None = None,
) -> FastAPI:
    repo = repository or create_repository_from_environment()
    foundation_repository = data_foundation_repository
    if foundation_repository is None:
        database_url = getattr(repo, "_database_url", None)
        if isinstance(repo, PostgresOperationsRepository) and isinstance(database_url, str):
            foundation_repository = PostgresDataFoundationRepository(database_url)
            if os.getenv("GOODMONEYING_RUNTIME_MODE") == "production":
                foundation_repository.assert_runtime_ready()
        else:
            foundation_repository = EmptyDataFoundationRepository()
    operator_token = os.getenv("GOODMONEYING_OPERATOR_TOKEN", "local-dev-token")
    service = OperationsService(repo, load_dashboard_refresh_seconds())
    app = FastAPI(title="goodmoneying 시스템 트레이딩 운영 API", version="0.2.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(HTTPException)
    def handle_http_exception(_request: Request, exc: HTTPException) -> JSONResponse:
        detail = cast(object, exc.detail)
        if isinstance(detail, dict) and "code" in detail and "message" in detail:
            content = detail
        else:
            content = {"code": "HTTP_ERROR", "message": str(detail)}
        return JSONResponse(status_code=exc.status_code, content=content, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    def handle_validation_exception(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"code": "VALIDATION_ERROR", "message": str(exc)},
        )

    def require_operator_token(
        x_operator_token: Annotated[str | None, Header(alias="X-Operator-Token")] = None,
    ) -> None:
        verify_operator_token(operator_token, x_operator_token)

    @app.get("/health", response_model=HealthResponse)
    def get_health() -> HealthResponse:
        return HealthResponse(status="ok", checkedAt=now_kst())

    @app.get("/v1/data-foundation", response_model=DataFoundationResponse)
    def get_data_foundation() -> DataFoundationResponse:
        overview = foundation_repository.overview()
        return DataFoundationResponse(
            timeZone="UTC",
            policyStartAt=overview.policy_start_at,
            summary=DataFoundationSummaryResponse(
                marketCount=overview.market_count,
                krwMarketCount=overview.krw_market_count,
                activeTargetCount=overview.active_target_count,
                pendingBackfillJobCount=overview.pending_backfill_job_count,
                desiredSubscriptionCount=overview.desired_subscription_count,
                coverageCounts=_coverage_counts_response(overview.coverage_counts),
            ),
            markets=[
                DataFoundationMarketResponse(
                    marketCode=market.market_code,
                    koreanName=market.korean_name,
                    englishName=market.english_name,
                    quoteCurrency=market.quote_currency,
                    tradingStatus=market.trading_status,
                    marketWarning=market.market_warning,
                    targetStatus=market.target_status,
                    activeDataTypeCount=market.active_data_type_count,
                    totalDataTypeCount=market.total_data_type_count,
                    coverageCounts=_coverage_counts_response(market.coverage_counts),
                    collectionPolicy=(
                        MarketCollectionPolicyResponse(
                            startAt=market.collection_policy.start_at,
                            dataTypes=list(market.collection_policy.data_types),
                            candleUnit=market.collection_policy.candle_unit,
                            retentionDays=market.collection_policy.retention_days,
                            priority=market.collection_policy.priority,
                            continuous=market.collection_policy.continuous,
                        )
                        if market.collection_policy is not None
                        else None
                    ),
                )
                for market in overview.markets
            ],
        )

    @app.patch(
        "/v1/data-foundation/markets/{marketCode}",
        response_model=UpdateMarketTargetStateResponse,
        dependencies=[Depends(require_operator_token)],
    )
    def update_market_target_state(
        marketCode: str,
        request: UpdateMarketTargetStateRequest,
    ) -> UpdateMarketTargetStateResponse:
        changed_at = datetime.now(UTC)
        try:
            changed_at = foundation_repository.set_market_target_state(
                marketCode,
                state=request.state,
                actor=request.actorId,
                reason=request.reason,
                changed_at=changed_at,
                request_id=request.requestId,
                idempotency_key=request.idempotencyKey,
                requested_at=request.requestedAt,
                policy=(
                    MarketCollectionPolicySettings(
                        start_at=request.policy.startAt,
                        data_types=request.policy.dataTypes,
                        candle_unit=request.policy.candleUnit,
                        retention_days=request.policy.retentionDays,
                        priority=request.policy.priority,
                        continuous=request.policy.continuous,
                    )
                    if request.policy is not None
                    else None
                ),
            )
        except IdempotencyConflictError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "IDEMPOTENCY_CONFLICT", "message": str(exc)},
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_MARKET_TARGET_STATE", "message": str(exc)},
            ) from exc
        return UpdateMarketTargetStateResponse(
            marketCode=marketCode,
            state=request.state,
            changedAt=changed_at,
        )

    @app.get("/v1/dashboard/summary", response_model=DashboardSummaryResponse)
    def get_dashboard_summary() -> DashboardSummaryResponse:
        return service.dashboard_summary()

    @app.get("/v1/dashboard/summary/stream")
    def stream_dashboard_summary(
        once: Annotated[bool, Query(description="테스트와 진단용 단일 이벤트 전송 여부")] = False,
    ) -> StreamingResponse:
        def events() -> Iterator[str]:
            while True:
                yield f"event: dashboard\ndata: {service.dashboard_summary().model_dump_json()}\n\n"
                if once:
                    break
                time.sleep(service.dashboard_stream_interval_seconds())

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.get("/v1/dashboard/overview", response_model=DashboardOverviewResponse)
    def get_dashboard_overview() -> DashboardOverviewResponse:
        return service.dashboard_overview()

    @app.get("/v1/dashboard/targets", response_model=DashboardTargetsResponse)
    def get_dashboard_targets(
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> DashboardTargetsResponse:
        return service.dashboard_targets(limit, offset)

    @app.get("/v1/dashboard/coverage", response_model=DashboardCoverageResponse)
    def get_dashboard_coverage(
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> DashboardCoverageResponse:
        return service.dashboard_coverage(limit, offset)

    @app.get(
        "/v1/dashboard/collection-activity",
        response_model=DashboardCollectionActivityResponse,
    )
    def get_dashboard_collection_activity() -> DashboardCollectionActivityResponse:
        return service.dashboard_collection_activity()

    @app.get("/v1/dashboard/realtime-heatmap", response_model=DashboardRealtimeHeatmapResponse)
    def get_dashboard_realtime_heatmap(
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> DashboardRealtimeHeatmapResponse:
        return service.dashboard_realtime_heatmap(limit, offset)

    @app.get(
        "/v1/dashboard/storage-breakdown",
        response_model=DashboardStorageBreakdownResponse,
    )
    def get_dashboard_storage_breakdown() -> DashboardStorageBreakdownResponse:
        return service.dashboard_storage_breakdown()

    @app.get("/v1/dashboard/operations-trend", response_model=DashboardOperationsTrendResponse)
    def get_dashboard_operations_trend() -> DashboardOperationsTrendResponse:
        return service.dashboard_operations_trend()

    @app.get("/v1/dashboard/missing-ranges", response_model=DashboardMissingRangesResponse)
    def get_dashboard_missing_ranges(
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> DashboardMissingRangesResponse:
        return service.dashboard_missing_ranges(limit, offset)

    @app.get(
        "/v1/dashboard/audit-log-summary",
        response_model=DashboardAuditLogSummaryResponse,
    )
    def get_dashboard_audit_log_summary() -> DashboardAuditLogSummaryResponse:
        return service.dashboard_audit_log_summary()

    @app.get("/v1/candidate-universe", response_model=CandidateUniverseResponse)
    def get_candidate_universe() -> CandidateUniverseResponse:
        return service.candidate_universe()

    @app.put(
        "/v1/collection-targets",
        response_model=CollectionTargetsResponse,
        dependencies=[Depends(require_operator_token)],
    )
    def update_collection_targets(
        request: UpdateCollectionTargetsRequest,
    ) -> CollectionTargetsResponse:
        try:
            return service.update_collection_targets(request.instrumentIds, request.reason)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_COLLECTION_TARGETS", "message": str(exc)},
            ) from exc

    @app.get("/v1/market-list", response_model=MarketListResponse)
    def get_market_list() -> MarketListResponse:
        return service.market_list()

    @app.get("/v1/market-list/stream")
    def stream_market_list(
        once: Annotated[bool, Query(description="테스트와 진단용 단일 이벤트 전송 여부")] = False,
    ) -> StreamingResponse:
        def events() -> Iterator[str]:
            while True:
                yield f"event: marketList\ndata: {service.market_list().model_dump_json()}\n\n"
                if once:
                    break
                time.sleep(service.market_list_stream_interval_seconds())

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.websocket("/v1/realtime/analysis")
    async def stream_coin_analysis(websocket: WebSocket) -> None:
        await websocket.accept()
        latest_subscription: dict[str, object] | None = None
        latest_candle: dict[str, object] | None = None

        async def send_message(message_type: str, **payload: object) -> None:
            await websocket.send_json(
                {"version": "1", "type": message_type, "sentAt": now_kst().isoformat(), **payload}
            )

        async def send_snapshot(subscription: dict[str, object]) -> None:
            nonlocal latest_candle
            try:
                snapshot = service.analysis_snapshot(
                    int(cast(int | str, subscription["instrumentId"])),
                    str(subscription["unit"]),
                    int(cast(int | str, subscription["rangeDays"])),
                )
            except AnalysisSubscriptionError as exc:
                await send_message("analysis.error", code=exc.code, message=str(exc))
                return
            candles = cast(list[object], snapshot["candles"])
            chunk_count = max(1, (len(candles) + 499) // 500)
            await send_message("analysis.session", subscriptionId=str(uuid4()))
            await send_message("analysis.instrument", instrument=snapshot["instrument"])
            for chunk_index in range(chunk_count):
                await send_message(
                    "analysis.chart",
                    unit=snapshot["unit"],
                    chunkIndex=chunk_index,
                    chunkCount=chunk_count,
                    candles=candles[chunk_index * 500 : (chunk_index + 1) * 500],
                )
            indicator_points = cast(list[object], snapshot["indicatorPoints"])
            indicator_chunk_count = max(1, (len(indicator_points) + 499) // 500)
            for chunk_index in range(indicator_chunk_count):
                await send_message(
                    "analysis.indicators",
                    chunkIndex=chunk_index,
                    chunkCount=indicator_chunk_count,
                    points=indicator_points[chunk_index * 500 : (chunk_index + 1) * 500],
                )
            await send_message("analysis.market", **cast(dict[str, object], snapshot["market"]))
            latest_candle = cast(dict[str, object], candles[-1]) if candles else None

        try:
            while True:
                try:
                    message = await wait_for(websocket.receive_json(), timeout=1)
                except builtins.TimeoutError:
                    if latest_subscription is not None:
                        try:
                            snapshot = service.analysis_snapshot(
                                int(cast(int | str, latest_subscription["instrumentId"])),
                                str(latest_subscription["unit"]),
                                int(cast(int | str, latest_subscription["rangeDays"])),
                            )
                        except AnalysisSubscriptionError:
                            continue
                        candles = cast(list[dict[str, object]], snapshot["candles"])
                        if candles and candles[-1] != latest_candle:
                            await send_message("analysis.candle.upsert", candle=candles[-1])
                            indicator_points = cast(list[object], snapshot["indicatorPoints"])
                            if indicator_points:
                                await send_message(
                                    "analysis.indicator.upsert",
                                    point=indicator_points[-1],
                                )
                            latest_candle = candles[-1]
                        market = cast(dict[str, object], snapshot["market"])
                        await send_message("analysis.market", **market)
                    continue
                if not isinstance(message, dict) or message.get("type") != "analysis.subscribe":
                    await send_message(
                        "analysis.error",
                        code="INVALID_MESSAGE",
                        message="analysis.subscribe 메시지가 필요합니다.",
                    )
                    continue
                try:
                    latest_subscription = {
                        "instrumentId": int(cast(int | str, message["instrumentId"])),
                        "unit": str(message["unit"]),
                        "rangeDays": int(cast(int | str, message["rangeDays"])),
                    }
                except KeyError, TypeError, ValueError:
                    await send_message(
                        "analysis.error",
                        code="INVALID_MESSAGE",
                        message="instrumentId, unit, rangeDays를 올바르게 입력해야 합니다.",
                    )
                    continue
                await send_snapshot(latest_subscription)
        except WebSocketDisconnect:
            return

    @app.websocket("/v1/realtime/system-management")
    async def stream_system_management(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            while True:
                await websocket.send_json(
                    {
                        "version": "1",
                        "type": "system.snapshot",
                        "sentAt": now_kst().isoformat(),
                        "payload": service.system_management_snapshot(),
                    }
                )
                try:
                    await wait_for(websocket.receive_text(), timeout=1)
                except builtins.TimeoutError:
                    continue
        except WebSocketDisconnect:
            return

    @app.get(
        "/v1/collection-targets/{instrumentId}/coverage-segments",
        response_model=CollectionCoverageSegmentsResponse,
    )
    def get_collection_coverage_segments(
        instrumentId: int,
    ) -> CollectionCoverageSegmentsResponse:
        return service.collection_coverage_segments(instrumentId)

    @app.get("/v1/instruments/{instrumentId}", response_model=InstrumentDetailResponse)
    def get_instrument_detail(instrumentId: int) -> InstrumentDetailResponse:
        detail = service.instrument_detail(instrumentId)
        if detail is None:
            raise HTTPException(
                status_code=404, detail={"code": "NOT_FOUND", "message": "거래 상품이 없습니다."}
            )
        return detail

    @app.get("/v1/instruments/{instrumentId}/candles", response_model=CandleSeriesResponse)
    def get_candles(
        instrumentId: int,
        unit: str,
        from_: Annotated[datetime, Query(alias="from")],
        to: datetime,
        pageSize: Annotated[int, Query(ge=1, le=500)] = 500,
        cursor: datetime | None = None,
    ) -> CandleSeriesResponse:
        try:
            return service.candles(instrumentId, unit, from_, to, page_size=pageSize, cursor=cursor)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_CANDLE_QUERY", "message": str(exc)},
            ) from exc

    @app.get(
        "/v1/instruments/{instrumentId}/ticker-snapshots",
        response_model=TickerSnapshotsResponse,
    )
    def get_ticker_snapshots(
        instrumentId: int,
        from_: Annotated[datetime, Query(alias="from")],
        to: datetime,
    ) -> TickerSnapshotsResponse:
        return service.ticker_snapshots(instrumentId, from_, to)

    @app.get(
        "/v1/instruments/{instrumentId}/orderbook-summaries",
        response_model=OrderbookSummariesResponse,
    )
    def get_orderbook_summaries(
        instrumentId: int,
        from_: Annotated[datetime, Query(alias="from")],
        to: datetime,
    ) -> OrderbookSummariesResponse:
        return service.orderbook_summaries(instrumentId, from_, to)

    @app.get("/v1/collection-runs", response_model=CollectionRunsResponse)
    def get_collection_runs(limit: int = 50) -> CollectionRunsResponse:
        return service.collection_runs(limit)

    @app.post(
        "/v1/backfill/plans",
        response_model=BackfillPlanResponse,
        dependencies=[Depends(require_operator_token)],
    )
    def create_backfill_plan(
        request: CreateBackfillPlanRequest,
    ) -> BackfillPlanResponse:
        try:
            return service.create_backfill_plan(
                request.dataType,
                request.targetStartAt,
                request.targetEndAt,
                request.instrumentIds,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_BACKFILL_PLAN", "message": str(exc)},
            ) from exc

    @app.get("/v1/backfill/jobs", response_model=BackfillJobsResponse)
    def get_backfill_jobs() -> BackfillJobsResponse:
        return BackfillJobsResponse(items=service.backfill_jobs())

    @app.post(
        "/v1/backfill/jobs",
        response_model=BackfillJobResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_operator_token)],
    )
    def create_backfill_job(
        request: CreateBackfillJobRequest,
    ) -> BackfillJobResponse:
        try:
            return service.create_backfill_job(
                request.dataType,
                request.targetStartAt,
                request.targetEndAt,
                request.instrumentIds,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_BACKFILL_JOB", "message": str(exc)},
            ) from exc

    @app.post(
        "/v1/backfill/jobs/{jobId}/{action}",
        response_model=BackfillJobResponse,
        dependencies=[Depends(require_operator_token)],
    )
    def control_backfill_job(
        jobId: int,
        action: str,
    ) -> BackfillJobResponse:
        try:
            return service.control_backfill_job(jobId, action)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_BACKFILL_CONTROL", "message": str(exc)},
            ) from exc

    @app.delete(
        "/v1/backfill/jobs/{jobId}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(require_operator_token)],
    )
    def delete_backfill_job(jobId: int) -> Response:
        try:
            service.delete_backfill_job(jobId)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_BACKFILL_DELETE", "message": str(exc)},
            ) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/v1/notifications", response_model=NotificationEventsResponse)
    def get_notification_events() -> NotificationEventsResponse:
        return service.notifications()

    return app


def _coverage_counts_response(
    counts: Mapping[CoverageState, int],
) -> CoverageCountsResponse:
    return CoverageCountsResponse(
        available=counts["available"],
        no_trade=counts["no_trade"],
        missing=counts["missing"],
        unavailable=counts["unavailable"],
        unverified=counts["unverified"],
    )


app = create_app()
