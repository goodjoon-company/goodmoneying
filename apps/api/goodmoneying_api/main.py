from __future__ import annotations

import os
from datetime import datetime
from typing import Annotated, cast

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from goodmoneying_api.dependencies import verify_operator_token
from goodmoneying_api.schemas import (
    ApproveBackfillJobRequest,
    BackfillJobResponse,
    BackfillJobsResponse,
    BackfillPlanResponse,
    CandidateUniverseResponse,
    CandleSeriesResponse,
    CollectionRunsResponse,
    CollectionTargetsResponse,
    CreateBackfillPlanRequest,
    DashboardSummaryResponse,
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
from goodmoneying_shared.time import now_utc
from goodmoneying_worker.collector import seed_repository
from goodmoneying_worker.upbit_client import FixtureUpbitClient


def create_repository_from_environment() -> OperationsRepository:
    database_url = os.getenv("GOODMONEYING_DATABASE_URL")
    if database_url and database_url.startswith(("postgres://", "postgresql://")):
        repository = PostgresOperationsRepository(database_url)
        try:
            repository.list_candidate_universe()
        except ValueError:
            seed_repository(repository, FixtureUpbitClient())
        return repository
    return create_seeded_repository()


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
    service = OperationsService(repo)
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
        return HealthResponse(status="ok", checkedAt=now_utc())

    @app.get("/v1/dashboard/summary", response_model=DashboardSummaryResponse)
    def get_dashboard_summary() -> DashboardSummaryResponse:
        return service.dashboard_summary()

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
    def approve_backfill_job(
        request: ApproveBackfillJobRequest,
    ) -> BackfillJobResponse:
        try:
            return service.approve_backfill_job(request.planId)
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

    @app.get("/v1/notifications", response_model=NotificationEventsResponse)
    def get_notification_events() -> NotificationEventsResponse:
        return service.notifications()

    return app


app = create_app()
