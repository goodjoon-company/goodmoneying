from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qsl

import jwt
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from goodmoneying_upbit_gateway.auth import build_query_string, query_hash

FAKE_ACCESS_KEY = "fake-e2e-access"
FAKE_SECRET_KEY = "e" * 64
calls: list[dict[str, Any]] = []
websocket_calls: list[dict[str, Any]] = []
reconnect_closed = False
app = FastAPI()


def _record(request: Request) -> None:
    calls.append(
        {
            "method": request.method,
            "path": request.url.path,
            "origin": request.headers.get("Origin"),
            "authorization": request.headers.get("Authorization"),
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
    return calls + websocket_calls


@app.websocket("/websocket/public")
async def public_websocket(websocket: WebSocket) -> None:
    await _serve_websocket(websocket, visibility="public")


@app.websocket("/websocket/private")
async def private_websocket(websocket: WebSocket) -> None:
    await _serve_websocket(websocket, visibility="private")


async def _serve_websocket(websocket: WebSocket, *, visibility: str) -> None:
    global reconnect_closed
    await websocket.accept()
    authorization = websocket.headers.get("Authorization")
    websocket_calls.append(
        {
            "method": "WEBSOCKET",
            "path": websocket.url.path,
            "origin": websocket.headers.get("Origin"),
            "authorization": authorization,
        }
    )
    if visibility == "private":
        if authorization is None or not authorization.startswith("Bearer "):
            await websocket.send_json(
                {"error": {"name": "INVALID_AUTH", "message": "인증 정보가 없습니다."}}
            )
            await websocket.close()
            return
        payload = jwt.decode(
            authorization.removeprefix("Bearer "), FAKE_SECRET_KEY, algorithms=["HS512"]
        )
        assert payload["access_key"] == FAKE_ACCESS_KEY
    active_subscriptions: list[dict[str, Any]] = []
    try:
        while True:
            request = await websocket.receive_json()
            websocket_calls.append(
                {
                    "method": "WEBSOCKET_MESSAGE",
                    "path": websocket.url.path,
                    "origin": websocket.headers.get("Origin"),
                    "authorization": None,
                    "body": request,
                }
            )
            ticket = request[0]["ticket"]
            format_name = request[-1].get("format", "DEFAULT")
            operation = request[1]
            if operation.get("method") == "LIST_SUBSCRIPTIONS":
                await websocket.send_json(
                    {
                        "method": "LIST_SUBSCRIPTIONS",
                        "result": active_subscriptions,
                        "ticket": ticket,
                    }
                )
                continue
            active_subscriptions = request[1:-1]
            code = next(
                (code for item in request[1:-1] for code in item.get("codes", [])), None
            )
            if code == "KRW-MALFORMED":
                await websocket.send_bytes(b"not-json")
            elif code == "KRW-ERROR":
                await websocket.send_json(
                    {"error": {"name": "WRONG_FORMAT", "message": "잘못된 요청"}}
                )
            elif code == "KRW-RECONNECT" and not reconnect_closed:
                reconnect_closed = True
                await websocket.close(code=1012)
                return
            elif visibility == "public":
                payload = {
                    "type": operation["type"],
                    "code": code,
                    "trade_price": 100,
                    "timestamp": 1,
                    "stream_type": "REALTIME",
                }
                encoded: Any = [payload] if format_name.endswith("LIST") else payload
                await websocket.send_bytes(json.dumps(encoded).encode())
            # private 구독은 실제 자산·주문 이벤트를 의도적으로 만들지 않는다.
    except WebSocketDisconnect:
        return


@app.get("/v1/market/all")
def markets(request: Request) -> JSONResponse:
    _record(request)
    return JSONResponse(
        status_code=200,
        content=[
            {"market": "KRW-BTC", "korean_name": "비트코인", "english_name": "Bitcoin"},
            {"market": "KRW-ETH", "korean_name": "이더리움", "english_name": "Ethereum"},
        ],
        headers={"Remaining-Req": "group=market; min=600; sec=9"},
    )


@app.get("/v1/candles/minutes/{unit}")
def minute_candles(unit: int, request: Request) -> JSONResponse:
    _record(request)
    market = request.query_params.get("market", "KRW-BTC")
    minute_base = 19 if "to" not in request.query_params else 9
    return JSONResponse(
        status_code=200,
        content=[
            {
                "market": market,
                "candle_date_time_utc": f"2026-07-15T00:{minute_base - index:02d}:00",
                "opening_price": 100 + index,
                "high_price": 110 + index,
                "low_price": 90 + index,
                "trade_price": 105 + index,
                "candle_acc_trade_volume": 10 + index,
                "candle_acc_trade_price": 1000 + index,
                "unit": unit,
            }
            for index in range(10)
        ],
        headers={"Remaining-Req": "group=candles; min=600; sec=9"},
    )


@app.get("/v1/ticker")
def ticker(request: Request) -> JSONResponse:
    _record(request)
    market = request.query_params.get("markets", "KRW-BTC").split(",", maxsplit=1)[0]
    return JSONResponse(
        status_code=200,
        content=[{
            "market": market,
            "trade_price": 150_000_000,
            "acc_trade_price_24h": 90_000_000_000,
        }],
        headers={"Remaining-Req": "group=ticker; min=600; sec=9"},
    )


@app.get("/v1/orderbook")
def orderbook(request: Request) -> JSONResponse:
    _record(request)
    market = request.query_params.get("markets", "KRW-BTC").split(",", maxsplit=1)[0]
    return JSONResponse(
        status_code=200,
        content=[{
            "market": market,
            "orderbook_units": [{
                "ask_price": 150_001_000,
                "ask_size": 0.2,
                "bid_price": 150_000_000,
                "bid_size": 0.3,
            }],
        }],
        headers={"Remaining-Req": "group=orderbook; min=600; sec=9"},
    )


@app.get("/v1/pockets")
def pockets(request: Request) -> JSONResponse:
    _record(request)
    _decode(request, "")
    return JSONResponse(status_code=200, content={"pocket": "fake"})


@app.get("/v1/pockets/api_keys")
def pocket_api_keys(request: Request) -> JSONResponse:
    _record(request)
    raw_query = request.scope["query_string"].decode()
    decoded_query = parse_qsl(raw_query, keep_blank_values=True)
    _decode(request, build_query_string(decoded_query))
    return JSONResponse(
        status_code=200,
        content={"raw_query": raw_query, "decoded_query": decoded_query},
    )


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
    content: dict[str, Any] = {"fake_order_test": True, "status": status}
    headers = {"Remaining-Req": "group=order-test; min=480; sec=7"}
    if status == 418:
        content["error"] = {"message": "Blocked for 1 seconds."}
        headers["Retry-After"] = "1"
    return JSONResponse(
        status_code=status,
        content=content,
        headers=headers,
    )


@app.post("/v1/orders")
def forbidden_real_order(request: Request) -> JSONResponse:
    _record(request)
    return JSONResponse(status_code=599, content={"unsafe": True})
