from __future__ import annotations

import builtins
import hashlib
import json
import os
import time
from asyncio import wait_for
from collections.abc import Iterator, Mapping, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from typing import Annotated, Protocol, cast
from uuid import uuid4

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Path,
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
    BacktestEquityPointsResponse,
    BacktestRunResponse,
    BacktestRunsResponse,
    BacktestTradesResponse,
    CandidateUniverseResponse,
    CandleSeriesResponse,
    CollectionCoverageSegmentsResponse,
    CollectionRunsResponse,
    CollectionTargetsResponse,
    CoverageCountsResponse,
    CreateBackfillJobRequest,
    CreateBackfillPlanRequest,
    CreateDatasetBuildRequest,
    CreateStrategyRequest,
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
    DatasetBuildResponse,
    DatasetBuildsResponse,
    DatasetCoverageResponse,
    DatasetSeriesResponse,
    DatasetVersionResponse,
    DatasetVersionsResponse,
    HealthResponse,
    IndicatorSeriesResponse,
    InstrumentDetailResponse,
    MarketCollectionPolicyResponse,
    MarketListResponse,
    MarketStatisticsResponse,
    MicrostructureStatisticsResponse,
    NotificationEventsResponse,
    OrderbookSummariesResponse,
    PublishStrategyVersionRequest,
    StrategyDefinitionResponse,
    StrategyValidationResponse,
    StrategyVersionResponse,
    StrategyVersionsResponse,
    TickerSnapshotsResponse,
    UpdateCollectionTargetsRequest,
    UpdateMarketTargetStateRequest,
    UpdateMarketTargetStateResponse,
    ValidateStrategyGraphRequest,
)
from goodmoneying_api.service import AnalysisSubscriptionError, OperationsService
from goodmoneying_shared.backtest_store import (
    BacktestCursorMismatchError,
    PostgresBacktestStore,
)
from goodmoneying_shared.data_foundation import (
    CoverageState,
    DataFoundationOverview,
    MarketCollectionPolicySettings,
)
from goodmoneying_shared.data_foundation_repository import (
    IdempotencyConflictError,
    PostgresDataFoundationRepository,
)
from goodmoneying_shared.dataset_version_store import (
    DatasetCursorMismatchError,
    DatasetIdempotencyConflictError,
    PostgresDatasetVersionStore,
)
from goodmoneying_shared.postgres_repository import PostgresOperationsRepository
from goodmoneying_shared.realtime_stream import (
    CursorExpiredError,
    RealtimeEnvelopeBuilder,
    StreamCursorContext,
    StreamCursorError,
    StreamMessageType,
    decode_stream_cursor,
    encode_stream_cursor,
)
from goodmoneying_shared.repository import OperationsRepository
from goodmoneying_shared.sqlite_repository import SQLiteOperationsRepository
from goodmoneying_shared.strategy_graph import validate_strategy_graph
from goodmoneying_shared.strategy_store import (
    PostgresStrategyStore,
    StrategyCursorMismatchError,
    StrategyIdempotencyConflictError,
)
from goodmoneying_shared.time import now_kst

ANALYSIS_SNAPSHOT_VERSION_PREFIX = "analysis-snapshot-v1"


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


def _stream_cursor_secret() -> str:
    secret = os.getenv("GOODMONEYING_STREAM_CURSOR_SECRET")
    if secret:
        return secret
    if os.getenv("GOODMONEYING_RUNTIME_MODE") == "production":
        raise RuntimeError("운영 모드는 GOODMONEYING_STREAM_CURSOR_SECRET 설정이 필요합니다.")
    return "local-dev-stream-cursor-secret"


def _stream_send_timeout_seconds() -> float:
    configured = os.getenv("GOODMONEYING_STREAM_SEND_TIMEOUT_SECONDS")
    if configured is None:
        return 2.0
    try:
        timeout_seconds = float(configured)
    except ValueError as exc:
        raise RuntimeError(
            "GOODMONEYING_STREAM_SEND_TIMEOUT_SECONDS는 초 단위 숫자여야 합니다."
        ) from exc
    if timeout_seconds <= 0:
        raise RuntimeError("GOODMONEYING_STREAM_SEND_TIMEOUT_SECONDS는 0보다 커야 합니다.")
    return timeout_seconds


def _analysis_stream_topic(subscription: Mapping[str, object]) -> str:
    return (
        "analysis.instrument:"
        f"{int(cast(int | str, subscription['instrumentId']))}:"
        f"{str(subscription['unit'])}:"
        f"{int(cast(int | str, subscription['rangeDays']))}"
    )


def _analysis_stream_snapshot_response(
    snapshot: Mapping[str, object],
    *,
    topic: str,
    scope: str,
    now: datetime,
) -> dict[str, object]:
    snapshot_version = _analysis_snapshot_version(snapshot)
    sequence = 1
    cursor_secret = _stream_cursor_secret()
    cursor_ttl = RealtimeEnvelopeBuilder(
        topic=topic,
        scope=scope,
        cursor_secret=cursor_secret,
    ).cursor_ttl
    expires_at = now + cursor_ttl
    cursor = encode_stream_cursor(
        StreamCursorContext(
            topic=topic,
            scope=scope,
            sequence=sequence,
            snapshot_version=snapshot_version,
            issued_at=now,
            expires_at=expires_at,
        ),
        cursor_secret,
    )
    return {
        "schema_version": "1.0",
        "topic": topic,
        "scope": scope,
        "sequence": sequence,
        "cursor": cursor,
        "snapshotVersion": snapshot_version,
        "issuedAt": now.isoformat().replace("+00:00", "Z"),
        "expiresAt": expires_at.isoformat().replace("+00:00", "Z"),
        "payload": {
            "type": "analysis.snapshot",
            **snapshot,
        },
    }


def _analysis_snapshot_version(snapshot: Mapping[str, object]) -> str:
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str).encode()
    return f"{ANALYSIS_SNAPSHOT_VERSION_PREFIX}:{hashlib.sha256(payload).hexdigest()}"


def _is_supported_analysis_snapshot_version(snapshot_version: str) -> bool:
    return snapshot_version == ANALYSIS_SNAPSHOT_VERSION_PREFIX or snapshot_version.startswith(
        f"{ANALYSIS_SNAPSHOT_VERSION_PREFIX}:"
    )


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


DatasetApiIdempotencyConflictError = DatasetIdempotencyConflictError


class DatasetVersionApiRepository(Protocol):
    def create_build(
        self,
        *,
        request_id: str,
        idempotency_key: str,
        actor_id: str,
        requested_at: datetime,
        reason: str,
        selection: Mapping[str, object],
        policies: Mapping[str, object],
    ) -> Mapping[str, object]: ...

    def get_build(self, build_id: int) -> Mapping[str, object] | None: ...

    def list_builds(
        self, *, page_size: int, cursor: str | None
    ) -> Mapping[str, object]: ...

    def get_version(self, dataset_version_id: int) -> Mapping[str, object] | None: ...

    def list_versions(
        self, *, page_size: int, cursor: str | None
    ) -> Mapping[str, object]: ...

    def get_coverage(self, dataset_version_id: int) -> Mapping[str, object] | None: ...

    def get_series(
        self,
        *,
        dataset_version_id: int,
        series_id: int,
        from_at: datetime,
        to_at: datetime,
        page_size: int,
        cursor: str | None,
    ) -> Mapping[str, object] | None: ...


class EmptyDatasetVersionRepository:
    def create_build(self, **_arguments: object) -> Mapping[str, object]:
        raise RuntimeError("데이터셋 버전 저장소가 구성되지 않았다.")

    def get_build(self, _build_id: int) -> Mapping[str, object] | None:
        return None

    def list_builds(self, **_arguments: object) -> Mapping[str, object]:
        return {"items": [], "nextCursor": None}

    def get_version(self, _dataset_version_id: int) -> Mapping[str, object] | None:
        return None

    def list_versions(self, **_arguments: object) -> Mapping[str, object]:
        return {"items": [], "nextCursor": None}

    def get_coverage(self, _dataset_version_id: int) -> Mapping[str, object] | None:
        return None

    def get_series(self, **_arguments: object) -> Mapping[str, object] | None:
        return None


class BacktestApiRepository(Protocol):
    def list_runs(self, *, page_size: int, cursor: str | None) -> Mapping[str, object]: ...

    def get_run(self, backtest_run_id: int) -> Mapping[str, object] | None: ...

    def list_run_trades(self, **arguments: object) -> Mapping[str, object] | None: ...

    def list_run_equity_points(self, **arguments: object) -> Mapping[str, object] | None: ...


class EmptyBacktestRepository:
    def list_runs(self, **_arguments: object) -> Mapping[str, object]:
        return {"items": [], "nextCursor": None}

    def get_run(self, _backtest_run_id: int) -> Mapping[str, object] | None:
        return None

    def list_run_trades(self, **_arguments: object) -> Mapping[str, object] | None:
        return None

    def list_run_equity_points(self, **_arguments: object) -> Mapping[str, object] | None:
        return None


class StrategyApiRepository(Protocol):
    def validate_graph(self, *, graph: Mapping[str, object]) -> Mapping[str, object]: ...

    def create_strategy(
        self,
        *,
        request_id: str,
        idempotency_key: str,
        actor_id: str,
        requested_at: datetime,
        reason: str,
        owner_id: str,
        name: str,
    ) -> Mapping[str, object]: ...

    def publish_version(
        self,
        *,
        strategy_id: int,
        request_id: str,
        idempotency_key: str,
        actor_id: str,
        requested_at: datetime,
        reason: str,
        graph: Mapping[str, object],
    ) -> Mapping[str, object]: ...

    def list_versions(
        self, *, strategy_id: int, page_size: int, cursor: str | None
    ) -> Mapping[str, object]: ...

    def get_version(self, strategy_version_id: int) -> Mapping[str, object] | None: ...


class EmptyStrategyRepository:
    def validate_graph(self, *, graph: Mapping[str, object]) -> Mapping[str, object]:
        return validate_strategy_graph(graph).to_api()

    def create_strategy(self, **_arguments: object) -> Mapping[str, object]:
        raise RuntimeError("전략 저장소가 구성되지 않았다.")

    def publish_version(self, **_arguments: object) -> Mapping[str, object]:
        raise RuntimeError("전략 저장소가 구성되지 않았다.")

    def list_versions(self, **_arguments: object) -> Mapping[str, object]:
        return {"items": [], "nextCursor": None}

    def get_version(self, _strategy_version_id: int) -> Mapping[str, object] | None:
        return None


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
    dataset_version_repository: DatasetVersionApiRepository | None = None,
    strategy_repository: StrategyApiRepository | None = None,
    backtest_repository: BacktestApiRepository | None = None,
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
    if dataset_version_repository is not None:
        dataset_repository = dataset_version_repository
    elif isinstance(repo, PostgresOperationsRepository):
        dataset_repository = PostgresDatasetVersionStore(repo)
    else:
        dataset_repository = EmptyDatasetVersionRepository()
    if strategy_repository is not None:
        strategy_store = strategy_repository
    elif isinstance(repo, PostgresOperationsRepository):
        strategy_store = PostgresStrategyStore(repo)
    else:
        strategy_store = EmptyStrategyRepository()
    if backtest_repository is not None:
        backtest_store = backtest_repository
    elif isinstance(repo, PostgresOperationsRepository):
        backtest_store = PostgresBacktestStore(repo)
    else:
        backtest_store = EmptyBacktestRepository()
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

    @app.post(
        "/v1/strategy-graphs/validate",
        response_model=StrategyValidationResponse,
        dependencies=[Depends(require_operator_token)],
    )
    def validate_strategy_graph_route(
        request: ValidateStrategyGraphRequest,
    ) -> StrategyValidationResponse:
        result = strategy_store.validate_graph(graph=request.graph.model_dump())
        return StrategyValidationResponse.model_validate(result)

    @app.post(
        "/v1/strategies",
        response_model=StrategyDefinitionResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_operator_token)],
    )
    def create_strategy(request: CreateStrategyRequest) -> StrategyDefinitionResponse:
        try:
            result = strategy_store.create_strategy(
                request_id=request.requestId,
                idempotency_key=request.idempotencyKey,
                actor_id=request.actorId,
                requested_at=request.requestedAt,
                reason=request.reason,
                owner_id=request.ownerId,
                name=request.name,
            )
        except StrategyIdempotencyConflictError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "STRATEGY_IDEMPOTENCY_CONFLICT", "message": str(exc)},
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": "INVALID_STRATEGY", "message": str(exc)},
            ) from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "STRATEGY_STORE_UNAVAILABLE", "message": str(exc)},
            ) from exc
        return StrategyDefinitionResponse.model_validate(result)

    @app.post(
        "/v1/strategies/{strategyId}/versions",
        response_model=StrategyVersionResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_operator_token)],
    )
    def publish_strategy_version(
        strategyId: Annotated[int, Path(gt=0)],
        request: PublishStrategyVersionRequest,
    ) -> StrategyVersionResponse:
        try:
            result = strategy_store.publish_version(
                strategy_id=strategyId,
                request_id=request.requestId,
                idempotency_key=request.idempotencyKey,
                actor_id=request.actorId,
                requested_at=request.requestedAt,
                reason=request.reason,
                graph=request.graph.model_dump(),
            )
        except StrategyIdempotencyConflictError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "STRATEGY_IDEMPOTENCY_CONFLICT", "message": str(exc)},
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": "INVALID_STRATEGY_GRAPH", "message": str(exc)},
            ) from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "STRATEGY_STORE_UNAVAILABLE", "message": str(exc)},
            ) from exc
        return StrategyVersionResponse.model_validate(result)

    @app.get(
        "/v1/strategies/{strategyId}/versions",
        response_model=StrategyVersionsResponse,
    )
    def list_strategy_versions(
        strategyId: Annotated[int, Path(gt=0)],
        pageSize: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: str | None = None,
    ) -> StrategyVersionsResponse:
        try:
            result = strategy_store.list_versions(
                strategy_id=strategyId, page_size=pageSize, cursor=cursor
            )
        except StrategyCursorMismatchError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "STRATEGY_CURSOR_CONTEXT_MISMATCH", "message": str(exc)},
            ) from exc
        return StrategyVersionsResponse.model_validate(result)

    @app.get(
        "/v1/strategy-versions/{strategyVersionId}",
        response_model=StrategyVersionResponse,
    )
    def get_strategy_version(
        strategyVersionId: Annotated[int, Path(gt=0)],
    ) -> StrategyVersionResponse:
        result = strategy_store.get_version(strategyVersionId)
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "STRATEGY_VERSION_NOT_FOUND",
                    "message": "전략 버전이 없습니다.",
                },
            )
        return StrategyVersionResponse.model_validate(result)

    @app.get("/v1/backtest-runs", response_model=BacktestRunsResponse)
    def list_backtest_runs(
        pageSize: Annotated[int, Query(ge=1, le=100)] = 25,
        cursor: str | None = None,
    ) -> BacktestRunsResponse:
        try:
            result = backtest_store.list_runs(page_size=pageSize, cursor=cursor)
        except BacktestCursorMismatchError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "BACKTEST_CURSOR_CONTEXT_MISMATCH",
                    "message": "백테스트 run 목록 cursor가 현재 조회 문맥과 다릅니다.",
                },
            ) from exc
        return BacktestRunsResponse.model_validate(result)

    @app.get(
        "/v1/backtest-runs/{backtestRunId}",
        response_model=BacktestRunResponse,
    )
    def get_backtest_run(
        backtestRunId: Annotated[int, Path(gt=0)],
    ) -> BacktestRunResponse:
        result = backtest_store.get_run(backtestRunId)
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "BACKTEST_RUN_NOT_FOUND",
                    "message": "백테스트 실행 결과가 없습니다.",
                },
            )
        return BacktestRunResponse.model_validate(result)

    @app.get(
        "/v1/backtest-runs/{backtestRunId}/trades",
        response_model=BacktestTradesResponse,
    )
    def list_backtest_trades(
        backtestRunId: Annotated[int, Path(gt=0)],
        pageSize: Annotated[int, Query(ge=1, le=500)] = 100,
        cursor: str | None = None,
    ) -> BacktestTradesResponse:
        try:
            result = backtest_store.list_run_trades(
                backtest_run_id=backtestRunId,
                page_size=pageSize,
                cursor=cursor,
            )
        except BacktestCursorMismatchError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "BACKTEST_RESULT_CURSOR_CONTEXT_MISMATCH",
                    "message": "백테스트 결과 cursor가 현재 조회 문맥과 다릅니다.",
                },
            ) from exc
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "BACKTEST_RUN_NOT_FOUND",
                    "message": "백테스트 실행 결과가 없습니다.",
                },
            )
        return BacktestTradesResponse.model_validate(result)

    @app.get(
        "/v1/backtest-runs/{backtestRunId}/equity-points",
        response_model=BacktestEquityPointsResponse,
    )
    def list_backtest_equity_points(
        backtestRunId: Annotated[int, Path(gt=0)],
        pageSize: Annotated[int, Query(ge=1, le=500)] = 100,
        cursor: str | None = None,
    ) -> BacktestEquityPointsResponse:
        try:
            result = backtest_store.list_run_equity_points(
                backtest_run_id=backtestRunId,
                page_size=pageSize,
                cursor=cursor,
            )
        except BacktestCursorMismatchError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "BACKTEST_RESULT_CURSOR_CONTEXT_MISMATCH",
                    "message": "백테스트 결과 cursor가 현재 조회 문맥과 다릅니다.",
                },
            ) from exc
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "BACKTEST_RUN_NOT_FOUND",
                    "message": "백테스트 실행 결과가 없습니다.",
                },
            )
        return BacktestEquityPointsResponse.model_validate(result)

    @app.post(
        "/v1/dataset-builds",
        response_model=DatasetBuildResponse,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_operator_token)],
    )
    def create_dataset_build(request: CreateDatasetBuildRequest) -> DatasetBuildResponse:
        try:
            result = dataset_repository.create_build(
                request_id=request.requestId,
                idempotency_key=request.idempotencyKey,
                actor_id=request.actorId,
                requested_at=request.requestedAt,
                reason=request.reason,
                selection=request.selection.model_dump(by_alias=True),
                policies=request.policies.model_dump(),
            )
        except DatasetApiIdempotencyConflictError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "DATASET_IDEMPOTENCY_CONFLICT", "message": str(exc)},
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": "INVALID_DATASET_BUILD", "message": str(exc)},
            ) from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "DATASET_STORE_UNAVAILABLE", "message": str(exc)},
            ) from exc
        return DatasetBuildResponse.model_validate(result)

    @app.get("/v1/dataset-builds", response_model=DatasetBuildsResponse)
    def list_dataset_builds(
        pageSize: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: str | None = None,
    ) -> DatasetBuildsResponse:
        try:
            result = dataset_repository.list_builds(page_size=pageSize, cursor=cursor)
        except DatasetCursorMismatchError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "DATASET_CURSOR_CONTEXT_MISMATCH", "message": str(exc)},
            ) from exc
        return DatasetBuildsResponse.model_validate(result)

    @app.get("/v1/dataset-builds/{buildId}", response_model=DatasetBuildResponse)
    def get_dataset_build(buildId: Annotated[int, Path(gt=0)]) -> DatasetBuildResponse:
        result = dataset_repository.get_build(buildId)
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "DATASET_BUILD_NOT_FOUND", "message": "생성 작업이 없습니다."},
            )
        return DatasetBuildResponse.model_validate(result)

    @app.get(
        "/v1/dataset-versions",
        response_model=DatasetVersionsResponse,
    )
    def list_dataset_versions(
        pageSize: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: str | None = None,
    ) -> DatasetVersionsResponse:
        try:
            result = dataset_repository.list_versions(page_size=pageSize, cursor=cursor)
        except DatasetCursorMismatchError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "DATASET_CURSOR_CONTEXT_MISMATCH",
                    "message": str(exc),
                },
            ) from exc
        return DatasetVersionsResponse.model_validate(result)

    @app.get(
        "/v1/dataset-versions/{datasetVersionId}",
        response_model=DatasetVersionResponse,
    )
    def get_dataset_version(
        datasetVersionId: Annotated[int, Path(gt=0)],
    ) -> DatasetVersionResponse:
        result = dataset_repository.get_version(datasetVersionId)
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "DATASET_VERSION_NOT_FOUND",
                    "message": "데이터셋 버전이 없습니다.",
                },
            )
        return DatasetVersionResponse.model_validate(result)

    @app.get(
        "/v1/dataset-versions/{datasetVersionId}/coverage",
        response_model=DatasetCoverageResponse,
    )
    def get_dataset_coverage(
        datasetVersionId: Annotated[int, Path(gt=0)],
    ) -> DatasetCoverageResponse:
        result = dataset_repository.get_coverage(datasetVersionId)
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "DATASET_VERSION_NOT_FOUND",
                    "message": "데이터셋 버전이 없습니다.",
                },
            )
        return DatasetCoverageResponse.model_validate(result)

    @app.get(
        "/v1/dataset-versions/{datasetVersionId}/series",
        response_model=DatasetSeriesResponse,
    )
    def get_dataset_series(
        datasetVersionId: Annotated[int, Path(gt=0)],
        seriesId: Annotated[int, Query(gt=0)],
        from_: Annotated[datetime, Query(alias="from")],
        to: datetime,
        pageSize: Annotated[int, Query(ge=1, le=500)] = 500,
        cursor: str | None = None,
    ) -> DatasetSeriesResponse:
        if (
            from_.tzinfo is None
            or from_.utcoffset() != UTC.utcoffset(from_)
            or to.tzinfo is None
            or to.utcoffset() != UTC.utcoffset(to)
            or from_ >= to
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "INVALID_DATASET_SERIES_RANGE",
                    "message": "series 범위는 UTC의 from < to 반개방 구간이어야 한다.",
                },
            )
        try:
            result = dataset_repository.get_series(
                dataset_version_id=datasetVersionId,
                series_id=seriesId,
                from_at=from_,
                to_at=to,
                page_size=pageSize,
                cursor=cursor,
            )
        except DatasetCursorMismatchError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "DATASET_CURSOR_CONTEXT_MISMATCH",
                    "message": str(exc),
                },
            ) from exc
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "DATASET_VERSION_NOT_FOUND",
                    "message": "데이터셋 series가 없습니다.",
                },
            )
        return DatasetSeriesResponse.model_validate(result)

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
                    instrumentId=market.instrument_id,
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

    @app.get("/v1/realtime/analysis/snapshot")
    def get_realtime_analysis_snapshot(
        instrumentId: Annotated[int, Query(gt=0)],
        unit: str,
        rangeDays: Annotated[int, Query(gt=0)],
    ) -> dict[str, object]:
        subscription = {"instrumentId": instrumentId, "unit": unit, "rangeDays": rangeDays}
        topic = _analysis_stream_topic(subscription)
        try:
            snapshot = service.analysis_snapshot(instrumentId, unit, rangeDays)
        except AnalysisSubscriptionError as exc:
            status_code = (
                status.HTTP_403_FORBIDDEN
                if exc.code == "NOT_WATCHLISTED"
                else status.HTTP_422_UNPROCESSABLE_CONTENT
            )
            raise HTTPException(
                status_code=status_code,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc
        return _analysis_stream_snapshot_response(
            snapshot,
            topic=topic,
            scope="operator:local",
            now=now_kst(),
        )

    @app.websocket("/v1/realtime/analysis/stream")
    @app.websocket("/v1/realtime/analysis")
    async def stream_coin_analysis(websocket: WebSocket) -> None:
        await websocket.accept()
        latest_subscription: dict[str, object] | None = None
        latest_candle: dict[str, object] | None = None
        latest_indicator_points: list[object] = []
        latest_microstructure_points: list[object] = []
        emit_snapshot_heartbeat = False
        send_timeout_seconds = _stream_send_timeout_seconds()
        stream_builder = RealtimeEnvelopeBuilder(
            topic="analysis.control",
            scope="operator:local",
            cursor_secret=_stream_cursor_secret(),
        )

        class SlowConsumerDisconnect(Exception):
            pass

        async def send_stream_json(message: dict[str, object]) -> None:
            try:
                await wait_for(websocket.send_json(message), timeout=send_timeout_seconds)
            except builtins.TimeoutError as exc:
                sent_at = now_kst()
                payload: dict[str, object] = {
                    "version": "1",
                    "type": "stream.slow_consumer",
                    "sentAt": sent_at.isoformat(),
                    "code": "SLOW_CONSUMER",
                    "message": "클라이언트 수신이 지연되어 REST snapshot 복구가 필요합니다.",
                    "lastSequence": stream_builder.sequence,
                }
                envelope = stream_builder.make(
                    message_type="slow_consumer",
                    payload=payload,
                    now=sent_at,
                    increment_sequence=False,
                )
                with suppress(Exception):
                    await wait_for(
                        websocket.send_json({**payload, **envelope}),
                        timeout=min(send_timeout_seconds, 0.25),
                    )
                with suppress(Exception):
                    await websocket.close(code=1013)
                raise SlowConsumerDisconnect from exc

        async def send_message(
            message_type: str,
            *,
            stream_message_type: str = "event",
            **payload: object,
        ) -> None:
            sent_at = now_kst()
            legacy_payload = {
                "version": "1",
                "type": message_type,
                "sentAt": sent_at.isoformat(),
                **payload,
            }
            envelope = stream_builder.make(
                message_type=cast(StreamMessageType, stream_message_type),
                payload=legacy_payload,
                now=sent_at,
            )
            await send_stream_json({**legacy_payload, **envelope})

        async def send_heartbeat() -> None:
            sent_at = now_kst()
            payload: dict[str, object] = {
                "version": "1",
                "type": "stream.heartbeat",
                "sentAt": sent_at.isoformat(),
                "lastSequence": stream_builder.sequence,
                "serverTime": sent_at.isoformat(),
            }
            envelope = stream_builder.make(
                message_type="heartbeat",
                payload=payload,
                now=sent_at,
                increment_sequence=False,
            )
            await send_stream_json({**payload, **envelope})

        async def send_snapshot_required(topic: str, message: str, code: str) -> None:
            nonlocal stream_builder
            stream_builder = RealtimeEnvelopeBuilder(
                topic=topic,
                scope="operator:local",
                cursor_secret=_stream_cursor_secret(),
            )
            sent_at = now_kst()
            payload: dict[str, object] = {
                "version": "1",
                "type": "analysis.snapshot_required",
                "sentAt": sent_at.isoformat(),
                "code": code,
                "message": message,
                "lastSequence": stream_builder.sequence,
                "snapshotTopic": topic,
            }
            envelope = stream_builder.make(
                message_type="snapshot_required",
                payload=payload,
                now=sent_at,
                increment_sequence=False,
            )
            await send_stream_json({**payload, **envelope})

        def remember_snapshot(snapshot: Mapping[str, object]) -> None:
            nonlocal latest_candle, latest_indicator_points, latest_microstructure_points
            candles = cast(list[object], snapshot["candles"])
            indicator_points = cast(list[object], snapshot["indicatorPoints"])
            microstructure_points = cast(list[object], snapshot["microstructurePoints"])
            latest_candle = cast(dict[str, object], candles[-1]) if candles else None
            latest_indicator_points = indicator_points
            latest_microstructure_points = microstructure_points

        async def send_snapshot(subscription: dict[str, object]) -> None:
            try:
                snapshot = service.analysis_snapshot(
                    int(cast(int | str, subscription["instrumentId"])),
                    str(subscription["unit"]),
                    int(cast(int | str, subscription["rangeDays"])),
                )
            except AnalysisSubscriptionError as exc:
                await send_message(
                    "analysis.error",
                    stream_message_type="error",
                    code=exc.code,
                    message=str(exc),
                )
                return
            stream_builder.snapshot_version = _analysis_snapshot_version(snapshot)
            candles = cast(list[object], snapshot["candles"])
            chunk_count = max(1, (len(candles) + 499) // 500)
            await send_message(
                "analysis.session",
                stream_message_type="subscribed",
                subscriptionId=str(uuid4()),
            )
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
            microstructure_points = cast(list[object], snapshot["microstructurePoints"])
            microstructure_chunk_count = max(1, (len(microstructure_points) + 499) // 500)
            for chunk_index in range(microstructure_chunk_count):
                await send_message(
                    "analysis.microstructure",
                    chunkIndex=chunk_index,
                    chunkCount=microstructure_chunk_count,
                    points=microstructure_points[chunk_index * 500 : (chunk_index + 1) * 500],
                )
            market_payload = cast(Mapping[str, object], snapshot["market"])
            await send_message(
                "analysis.market",
                ticker=market_payload["ticker"],
                orderbook=market_payload["orderbook"],
                tradeSummary=market_payload["tradeSummary"],
            )
            if emit_snapshot_heartbeat:
                await send_heartbeat()
            remember_snapshot(snapshot)

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
                        indicator_points = cast(list[object], snapshot["indicatorPoints"])
                        microstructure_points = cast(list[object], snapshot["microstructurePoints"])
                        if candles and candles[-1] != latest_candle:
                            await send_message("analysis.candle.upsert", candle=candles[-1])
                            latest_candle = candles[-1]
                        indicator_update = _classify_indicator_stream_update(
                            latest_indicator_points,
                            indicator_points,
                            candles[-1] if candles else None,
                        )
                        if indicator_update == "upsert":
                            await send_message(
                                "analysis.indicator.upsert",
                                point=indicator_points[-1],
                            )
                            latest_indicator_points = indicator_points
                        elif indicator_update == "cache":
                            latest_indicator_points = indicator_points
                        elif indicator_update == "refresh":
                            chunk_count = max(1, (len(indicator_points) + 499) // 500)
                            for chunk_index in range(chunk_count):
                                await send_message(
                                    "analysis.indicators",
                                    chunkIndex=chunk_index,
                                    chunkCount=chunk_count,
                                    revisionRefresh=True,
                                    points=indicator_points[
                                        chunk_index * 500 : (chunk_index + 1) * 500
                                    ],
                                )
                            latest_indicator_points = indicator_points
                        microstructure_update = _classify_microstructure_stream_update(
                            latest_microstructure_points,
                            microstructure_points,
                            candles[-1] if candles else None,
                        )
                        if microstructure_update == "upsert":
                            await send_message(
                                "analysis.microstructure.upsert",
                                point=microstructure_points[-1],
                            )
                            latest_microstructure_points = microstructure_points
                        elif microstructure_update == "cache":
                            latest_microstructure_points = microstructure_points
                        elif microstructure_update == "refresh":
                            chunk_count = max(1, (len(microstructure_points) + 499) // 500)
                            for chunk_index in range(chunk_count):
                                await send_message(
                                    "analysis.microstructure",
                                    chunkIndex=chunk_index,
                                    chunkCount=chunk_count,
                                    revisionRefresh=True,
                                    points=microstructure_points[
                                        chunk_index * 500 : (chunk_index + 1) * 500
                                    ],
                                )
                            latest_microstructure_points = microstructure_points
                        market = cast(dict[str, object], snapshot["market"])
                        await send_message(
                            "analysis.market",
                            ticker=market["ticker"],
                            orderbook=market["orderbook"],
                            tradeSummary=market["tradeSummary"],
                        )
                        await send_heartbeat()
                    continue
                if not isinstance(message, dict):
                    await send_message(
                        "analysis.error",
                        stream_message_type="error",
                        code="INVALID_MESSAGE",
                        message="analysis.subscribe 메시지가 필요합니다.",
                    )
                    continue
                is_legacy_subscribe = message.get("type") == "analysis.subscribe"
                is_stream_subscribe = (
                    message.get("schema_version") == "1.0"
                    and message.get("message_type") == "subscribe"
                )
                if not (is_legacy_subscribe or is_stream_subscribe):
                    await send_message(
                        "analysis.error",
                        stream_message_type="error",
                        code="INVALID_MESSAGE",
                        message="analysis.subscribe 메시지가 필요합니다.",
                    )
                    continue
                try:
                    emit_snapshot_heartbeat = is_stream_subscribe
                    command_payload = (
                        cast(Mapping[str, object], message["payload"])
                        if isinstance(message.get("payload"), Mapping)
                        else message
                    )
                    latest_subscription = {
                        "instrumentId": int(cast(int | str, command_payload["instrumentId"])),
                        "unit": str(command_payload["unit"]),
                        "rangeDays": int(cast(int | str, command_payload["rangeDays"])),
                    }
                except KeyError, TypeError, ValueError:
                    await send_message(
                        "analysis.error",
                        stream_message_type="error",
                        code="INVALID_MESSAGE",
                        message="instrumentId, unit, rangeDays를 올바르게 입력해야 합니다.",
                    )
                    continue
                topic = _analysis_stream_topic(latest_subscription)
                stream_builder = RealtimeEnvelopeBuilder(
                    topic=topic,
                    scope="operator:local",
                    cursor_secret=_stream_cursor_secret(),
                )
                if is_stream_subscribe and (
                    message.get("topic") not in {None, topic}
                    or message.get("scope") not in {None, "operator:local"}
                ):
                    await send_message(
                        "analysis.error",
                        stream_message_type="error",
                        code="INVALID_TOPIC",
                        message="구독 topic/scope가 payload와 일치하지 않습니다.",
                    )
                    continue
                resume_cursor = message.get("resumeCursor") or message.get("resume_cursor")
                cursor_context: StreamCursorContext | None = None
                if resume_cursor is not None:
                    try:
                        cursor_context = decode_stream_cursor(
                            str(resume_cursor),
                            _stream_cursor_secret(),
                            topic=topic,
                            scope="operator:local",
                            now=now_kst(),
                        )
                    except CursorExpiredError as exc:
                        await send_snapshot_required(
                            topic,
                            f"{exc} REST snapshot 복구가 필요합니다.",
                            "CURSOR_EXPIRED",
                        )
                        continue
                    except StreamCursorError as exc:
                        await send_snapshot_required(
                            topic,
                            f"{exc} REST snapshot 복구가 필요합니다.",
                            "CURSOR_INVALID",
                        )
                        continue
                if cursor_context is not None:
                    try:
                        snapshot = service.analysis_snapshot(
                            int(cast(int | str, latest_subscription["instrumentId"])),
                            str(latest_subscription["unit"]),
                            int(cast(int | str, latest_subscription["rangeDays"])),
                        )
                    except AnalysisSubscriptionError as exc:
                        await send_message(
                            "analysis.error",
                            stream_message_type="error",
                            code=exc.code,
                            message=str(exc),
                        )
                        continue
                    current_snapshot_version = _analysis_snapshot_version(snapshot)
                    if not _is_supported_analysis_snapshot_version(
                        cursor_context.snapshot_version
                    ):
                        await send_snapshot_required(
                            topic,
                            (
                                "지원하지 않는 snapshot version cursor입니다. "
                                "REST snapshot 복구가 필요합니다."
                            ),
                            "CURSOR_INVALID",
                        )
                        continue
                    stream_builder.snapshot_version = current_snapshot_version
                    if cursor_context.snapshot_version != current_snapshot_version:
                        await send_snapshot(latest_subscription)
                        continue
                    try:
                        stream_builder.resume_from(cursor_context)
                    except StreamCursorError as exc:
                        await send_snapshot_required(
                            topic,
                            f"{exc} REST snapshot 복구가 필요합니다.",
                            "CURSOR_INVALID",
                        )
                        continue
                    remember_snapshot(snapshot)
                    await send_message(
                        "analysis.session",
                        stream_message_type="subscribed",
                        subscriptionId=str(uuid4()),
                        lastSequence=stream_builder.sequence,
                    )
                    await send_heartbeat()
                else:
                    await send_snapshot(latest_subscription)
        except (WebSocketDisconnect, SlowConsumerDisconnect):
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
        "/v1/instruments/{instrumentId}/indicators",
        response_model=IndicatorSeriesResponse,
    )
    def get_indicators(
        instrumentId: int,
        unit: str,
        from_: Annotated[datetime, Query(alias="from")],
        to: datetime,
        asOf: datetime,
        definitionSetHash: str | None = None,
        pageSize: Annotated[int, Query(ge=1, le=500)] = 500,
        cursor: str | None = None,
    ) -> IndicatorSeriesResponse:
        try:
            return service.indicator_points(
                instrumentId,
                unit,
                from_,
                to,
                as_of=asOf,
                page_size=pageSize,
                cursor=cursor,
                definition_version=definitionSetHash,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_INDICATOR_QUERY", "message": str(exc)},
            ) from exc

    @app.get(
        "/v1/instruments/{instrumentId}/market-statistics",
        response_model=MarketStatisticsResponse,
    )
    def get_market_statistics(
        instrumentId: int,
        unit: str,
        from_: Annotated[datetime, Query(alias="from")],
        to: datetime,
        asOf: datetime,
        calculationVersion: str | None = None,
        pageSize: Annotated[int, Query(ge=1, le=500)] = 500,
        cursor: str | None = None,
    ) -> MarketStatisticsResponse:
        try:
            return service.market_statistics(
                instrumentId,
                unit,
                from_,
                to,
                as_of=asOf,
                page_size=pageSize,
                cursor=cursor,
                calculation_version=calculationVersion,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_INDICATOR_QUERY", "message": str(exc)},
            ) from exc

    @app.get(
        "/v1/instruments/{instrumentId}/microstructure-statistics",
        response_model=MicrostructureStatisticsResponse,
    )
    def get_microstructure_statistics(
        instrumentId: int,
        unit: str,
        from_: Annotated[datetime, Query(alias="from")],
        to: datetime,
        asOf: datetime,
        calculationVersion: str | None = None,
        pageSize: Annotated[int, Query(ge=1, le=500)] = 500,
        cursor: str | None = None,
    ) -> MicrostructureStatisticsResponse:
        try:
            return service.microstructure_statistics(
                instrumentId,
                unit,
                from_,
                to,
                as_of=asOf,
                page_size=pageSize,
                cursor=cursor,
                calculation_version=calculationVersion,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_MICROSTRUCTURE_QUERY", "message": str(exc)},
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


def _classify_indicator_stream_update(
    previous: Sequence[object],
    current: Sequence[object],
    latest_candle: Mapping[str, object] | None,
) -> str:
    if current == previous:
        return "none"
    if not current:
        return "refresh"
    latest_point = current[-1]
    if not isinstance(latest_point, Mapping) or latest_candle is None:
        return "refresh"
    appended_latest = len(current) == len(previous) + 1 and current[:-1] == previous
    replaced_latest = len(current) == len(previous) and current[:-1] == previous[:-1]
    if latest_point.get("startedAt") != latest_candle.get("startedAt"):
        return "cache" if appended_latest else "refresh"
    return "upsert" if appended_latest or replaced_latest else "refresh"


def _classify_microstructure_stream_update(
    previous: Sequence[object],
    current: Sequence[object],
    latest_candle: Mapping[str, object] | None,
) -> str:
    """저장된 1분 미시구조 시계열의 최신 갱신과 과거 정정을 구분한다."""

    return _classify_indicator_stream_update(previous, current, latest_candle)

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
