from datetime import date
from typing import Annotated, Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import AnyUrl, BaseModel, ConfigDict, Field

from goodmoneying_upbit_gateway.catalog import endpoint_by_id, load_catalog


class GatewayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint_id: str = Field(pattern=r"^(rest|websocket)\.")
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
    code: str
    message: str


class ErrorResponse(BaseModel):
    detail: ErrorDetail


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {"model": ErrorResponse, "description": "비파괴 정책 차단"},
    404: {"model": ErrorResponse, "description": "알 수 없는 endpoint_id"},
}


def create_app() -> FastAPI:
    catalog = load_catalog()
    app = FastAPI(title="goodmoneying 업비트 API 게이트웨이", version="0.1.0")

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
        status_code=501,
        response_model=ErrorResponse,
        responses=ERROR_RESPONSES,
    )
    def execute_request(request: GatewayRequest) -> None:
        endpoint = endpoint_by_id(catalog, request.endpoint_id)
        if endpoint is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "UNKNOWN_ENDPOINT",
                    "message": "카탈로그에 없는 endpoint_id입니다.",
                },
            )
        if endpoint["safety"] == "blocked":
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "POLICY_BLOCKED",
                    "message": "비파괴 안전 정책에 따라 업비트 상향 호출을 차단했습니다.",
                },
            )
        raise HTTPException(
            status_code=501,
            detail={
                "code": "UPSTREAM_NOT_IMPLEMENTED",
                "message": "Issue #19 범위에서는 업비트 상향 호출을 수행하지 않습니다.",
            },
        )

    return app


app = create_app()
