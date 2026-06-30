from __future__ import annotations

import os
import time
from collections.abc import Iterator
from datetime import datetime
from typing import Annotated, cast

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
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
    HealthResponse,
    InstrumentDetailResponse,
    MarketListResponse,
    NotificationEventsResponse,
    OrderbookSummariesResponse,
    TickerSnapshotsResponse,
    UpdateCollectionTargetsRequest,
)
from goodmoneying_api.service import OperationsService
from goodmoneying_shared.postgres_repository import PostgresOperationsRepository
from goodmoneying_shared.repository import OperationsRepository
from goodmoneying_shared.sqlite_repository import SQLiteOperationsRepository
from goodmoneying_shared.time import now_kst
from goodmoneying_worker.collector import seed_repository
from goodmoneying_worker.upbit_client import FixtureUpbitClient


def create_repository_from_environment() -> OperationsRepository:
    database_url = os.getenv("GOODMONEYING_DATABASE_URL")
    if os.getenv("GOODMONEYING_DEMO_DATA") == "1":
        return create_seeded_repository()
    if database_url and database_url.startswith(("postgres://", "postgresql://")):
        return PostgresOperationsRepository(database_url)
    return SQLiteOperationsRepository()


def create_seeded_repository() -> SQLiteOperationsRepository:
    repository = SQLiteOperationsRepository()
    seed_repository(repository, FixtureUpbitClient())
    repository.add_notification(
        "info",
        "collector_bootstrap",
        "M1 fixture 수집 완료",
        "후보 유니버스와 기본 활성 수집 대상 50개를 fixture로 준비했습니다.",
    )
    return repository


def create_app(repository: OperationsRepository | None = None) -> FastAPI:
    repo = repository or create_repository_from_environment()
    operator_token = os.getenv("GOODMONEYING_OPERATOR_TOKEN", "local-dev-token")
    service = OperationsService(repo, load_dashboard_refresh_seconds())
    app = FastAPI(title="goodmoneying M1 Operations API", version="0.1.0")
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
    def handle_validation_exception(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
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
    ) -> CandleSeriesResponse:
        try:
            return service.candles(instrumentId, unit, from_, to)
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


app = create_app()
