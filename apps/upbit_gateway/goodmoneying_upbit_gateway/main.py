from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from goodmoneying_upbit_gateway.catalog import load_catalog


class GatewayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint_id: str = Field(pattern=r"^(rest|websocket)\.")
    parameters: dict[str, Any]


def create_app() -> FastAPI:
    catalog = load_catalog()
    app = FastAPI(title="goodmoneying 업비트 API 게이트웨이", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "service": "upbit-gateway",
            "catalog_version": str(catalog["catalog_version"]),
        }

    @app.get("/v1/catalog")
    def get_catalog() -> dict[str, Any]:
        return catalog

    @app.post("/v1/requests")
    def execute_request(request: GatewayRequest) -> None:
        del request
        raise HTTPException(
            status_code=501,
            detail={
                "code": "UPSTREAM_NOT_IMPLEMENTED",
                "message": "Issue #19 범위에서는 업비트 상향 호출을 수행하지 않습니다.",
            },
        )

    return app


app = create_app()
