import os
from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import httpx
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import AnyUrl, BaseModel, ConfigDict, Field

from goodmoneying_upbit_gateway.auth import CredentialConfigurationError, load_credentials
from goodmoneying_upbit_gateway.catalog import load_catalog, rest_endpoint_by_id
from goodmoneying_upbit_gateway.client import InvalidParameters
from goodmoneying_upbit_gateway.executor import (
    UpbitExecutor,
    UpstreamConnectionError,
    UpstreamProtocolError,
    UpstreamTimeout,
)
from goodmoneying_upbit_gateway.rate_limit import GroupRateLimiter, rate_limits_from_catalog
from goodmoneying_upbit_gateway.safety import PolicyBlocked
from goodmoneying_upbit_gateway.websocket_protocol import WebSocketRateLimiter
from goodmoneying_upbit_gateway.websocket_session import (
    GatewayWebSocketSession,
    WebSocketUpstreamSettings,
)


class GatewayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint_id: str = Field(pattern=r"^rest\.")
    parameters: dict[str, Any]


class RestInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active_count: Literal[50]
    deprecated_count: Literal[1]
    total_count: Literal[51]


class CatalogEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    endpoint_id: str = Field(pattern=r"^(rest|websocket)\.")
    safety: Literal["read", "test", "blocked"]
    source_url: AnyUrl


class Health(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"]
    service: Literal["upbit-gateway"]
    catalog_version: Literal["1.6.3"]


class UpbitApiCatalog(BaseModel):
    model_config = ConfigDict(extra="allow")

    catalog_version: Literal["1.6.3"]
    verified_at: date
    official_baseline: AnyUrl
    rest_inventory: RestInventory
    rest_endpoints: Annotated[list[CatalogEntry], Field(min_length=51, max_length=51)]
    websocket_streams: Annotated[list[CatalogEntry], Field(min_length=14, max_length=14)]
    websocket_operations: Annotated[list[CatalogEntry], Field(min_length=1, max_length=1)]
    gateway_websocket_operations: list[
        Literal["connect", "subscribe", "pause", "unsubscribe", "reconnect", "list"]
    ]


class ErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detail: ErrorDetail


class TraceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: str
    path: str
    parameters: dict[str, Any]


class TraceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status_code: int
    body: Any


class TraceRateLimit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group: str
    remaining_sec: int | None
    retry_after: str | None


class TraceEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: UUID
    endpoint_id: str
    request: TraceRequest
    response: TraceResponse
    rate_limit: TraceRateLimit
    duration_ms: float = Field(ge=0)
    received_at: datetime


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {"model": ErrorResponse, "description": "비파괴 정책 차단"},
    404: {"model": ErrorResponse, "description": "알 수 없는 endpoint_id"},
    422: {"model": ErrorResponse, "description": "카탈로그 파라미터 오류"},
    502: {"model": ErrorResponse, "description": "상향 응답 프로토콜 오류"},
    503: {"model": ErrorResponse, "description": "인증 설정 오류"},
    504: {"model": ErrorResponse, "description": "상향 호출 시간 초과"},
}

TRACE_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": TraceEnvelope, "description": "업비트 상향 JSON 응답과 마스킹된 추적"}
    for status in (201, 400, 401, 418, 429, 500)
}

MIXED_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {
        "model": TraceEnvelope | ErrorResponse,
        "description": "로컬 게이트웨이 오류 또는 상태를 보존한 업비트 상향 응답",
    }
    for status in (403, 404, 422, 502, 503, 504)
}

DEFAULT_TRACE_RESPONSE: dict[int | str, dict[str, Any]] = {
    "default": {
        "model": TraceEnvelope,
        "description": "명시되지 않은 업비트 상향 상태의 마스킹된 추적",
    }
}


def _default_executor(catalog: dict[str, Any]) -> UpbitExecutor:
    allow_loopback = os.environ.get("UPBIT_GATEWAY_ALLOW_LOOPBACK_TEST") == "true"
    return UpbitExecutor(
        http_client=httpx.Client(
            timeout=float(os.environ.get("UPBIT_GATEWAY_TIMEOUT_SECONDS", "10"))
        ),
        credentials_provider=lambda: load_credentials(os.environ),
        limiter=GroupRateLimiter(limits=rate_limits_from_catalog(catalog)),
        base_url=os.environ.get("UPBIT_GATEWAY_BASE_URL", "https://api.upbit.com"),
        allow_loopback_test=allow_loopback,
    )


def create_app(*, executor: UpbitExecutor | None = None) -> FastAPI:
    catalog = load_catalog()
    request_executor = executor or _default_executor(catalog)
    app = FastAPI(title="goodmoneying 업비트 API 게이트웨이", version="0.1.0")
    websocket_connect_limit = catalog["rate_limits"]["websocket-connect"]
    websocket_connect_limiter = WebSocketRateLimiter(
        per_second=websocket_connect_limit["requests"],
        per_minute=websocket_connect_limit["requests"] * 60,
    )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        del request, exc
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "code": "INVALID_REQUEST",
                    "message": "요청 본문이 게이트웨이 계약과 다릅니다.",
                }
            },
        )

    @app.get("/health", response_model=Health)
    def health() -> Health:
        return Health.model_validate(
            {
                "status": "ok",
                "service": "upbit-gateway",
                "catalog_version": catalog["catalog_version"],
            }
        )

    @app.get("/v1/catalog", response_model=UpbitApiCatalog)
    def get_catalog() -> UpbitApiCatalog:
        return UpbitApiCatalog.model_validate(catalog)

    @app.post(
        "/v1/requests",
        response_model=TraceEnvelope,
        responses=(ERROR_RESPONSES | TRACE_RESPONSES | MIXED_RESPONSES | DEFAULT_TRACE_RESPONSE),
    )
    def execute_request(payload: GatewayRequest, request: Request) -> JSONResponse:
        endpoint = rest_endpoint_by_id(catalog, payload.endpoint_id)
        if endpoint is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "UNKNOWN_ENDPOINT",
                    "message": "카탈로그에 없는 endpoint_id입니다.",
                },
            )
        try:
            result = request_executor.execute(
                endpoint,
                payload.parameters,
                incoming_headers=request.headers,
            )
        except PolicyBlocked as exc:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "POLICY_BLOCKED",
                    "message": str(exc),
                },
            ) from exc
        except InvalidParameters as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "INVALID_PARAMETERS", "message": str(exc)},
            ) from exc
        except CredentialConfigurationError as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "CREDENTIALS_NOT_CONFIGURED", "message": str(exc)},
            ) from exc
        except UpstreamTimeout as exc:
            raise HTTPException(
                status_code=504,
                detail={"code": "UPSTREAM_TIMEOUT", "message": str(exc)},
            ) from exc
        except UpstreamProtocolError as exc:
            raise HTTPException(
                status_code=502,
                detail={"code": "UPSTREAM_NON_JSON", "message": str(exc)},
            ) from exc
        except UpstreamConnectionError as exc:
            raise HTTPException(
                status_code=502,
                detail={"code": "UPSTREAM_CONNECTION_ERROR", "message": str(exc)},
            ) from exc
        return JSONResponse(status_code=result.status_code, content=result.envelope)

    @app.websocket("/v1/websocket")
    async def websocket_relay(websocket: WebSocket) -> None:
        await websocket.accept()
        session = GatewayWebSocketSession(
            downstream=websocket,
            catalog=catalog,
            settings=WebSocketUpstreamSettings.from_environment(catalog, os.environ),
            connect_limiter=websocket_connect_limiter,
        )
        try:
            while True:
                payload = await websocket.receive_json()
                if isinstance(payload, dict):
                    await session.handle(payload)
                else:
                    await session.handle({"action": "invalid", "request_id": "invalid-message"})
        except WebSocketDisconnect:
            pass
        finally:
            await session.close(notify=False)

    return app


app = create_app()
