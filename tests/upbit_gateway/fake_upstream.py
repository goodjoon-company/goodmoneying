from __future__ import annotations

from typing import Any

import jwt
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from goodmoneying_upbit_gateway.auth import build_query_string, query_hash

FAKE_ACCESS_KEY = "fake-e2e-access"
FAKE_SECRET_KEY = "e" * 64
calls: list[dict[str, Any]] = []
app = FastAPI()


def _record(request: Request) -> None:
    calls.append(
        {
            "method": request.method,
            "path": request.url.path,
            "origin": request.headers.get("Origin"),
        }
    )


def _decode(request: Request, query_string: str) -> dict[str, Any]:
    token = request.headers["Authorization"].removeprefix("Bearer ")
    payload: dict[str, Any] = jwt.decode(token, FAKE_SECRET_KEY, algorithms=["HS512"])
    assert payload["access_key"] == FAKE_ACCESS_KEY
    assert payload["nonce"]
    if query_string:
        assert payload["query_hash"] == query_hash(query_string)
        assert payload["query_hash_alg"] == "SHA512"
    return payload


@app.get("/__calls")
def get_calls() -> list[dict[str, Any]]:
    return calls


@app.get("/v1/market/all")
def markets(request: Request) -> JSONResponse:
    _record(request)
    return JSONResponse(
        status_code=200,
        content=[{"market": "KRW-BTC"}],
        headers={"Remaining-Req": "group=market; min=600; sec=9"},
    )


@app.get("/v1/pockets")
def pockets(request: Request) -> JSONResponse:
    _record(request)
    _decode(request, "")
    return JSONResponse(status_code=200, content={"pocket": "fake"})


@app.get("/v1/accounts")
def unauthorized(request: Request) -> JSONResponse:
    _record(request)
    _decode(request, "")
    return JSONResponse(status_code=401, content={"error": {"name": "unauthorized"}})


@app.post("/v1/orders/test")
async def order_test(request: Request) -> JSONResponse:
    _record(request)
    body = await request.json()
    _decode(request, build_query_string(list(body.items())))
    status_by_price = {"1000": 201, "400": 400, "429": 429, "418": 418}
    status = status_by_price[body["price"]]
    return JSONResponse(
        status_code=status,
        content={"fake_order_test": True, "status": status},
        headers={"Remaining-Req": "group=order-test; min=480; sec=7"},
    )


@app.post("/v1/orders")
def forbidden_real_order(request: Request) -> JSONResponse:
    _record(request)
    return JSONResponse(status_code=599, content={"unsafe": True})
