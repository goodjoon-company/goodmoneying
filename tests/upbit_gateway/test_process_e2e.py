from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
from websockets.typing import Origin

ROOT = Path(__file__).resolve().parents[2]
FAKE_SECRET_KEY = "e" * 64
E2E_OPERATOR_TOKEN = "e2e-operator-token"


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_ready(url: str, process: subprocess.Popen[str]) -> None:
    for _ in range(100):
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(f"프로세스 조기 종료\nstdout={stdout}\nstderr={stderr}")
        try:
            if httpx.get(url, timeout=0.2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.05)
    raise AssertionError(f"준비 확인 실패: {url}")


@contextmanager
def _processes() -> Iterator[tuple[str, str, subprocess.Popen[str], subprocess.Popen[str]]]:
    fake_port = _free_port()
    gateway_port = _free_port()
    environment = os.environ.copy()
    for key in (
        "UPBIT_ACCESS_KEY",
        "UPBIT_SECRET_KEY",
        "UPBIT_ACCESS_KEY_FILE",
        "UPBIT_SECRET_KEY_FILE",
    ):
        environment.pop(key, None)
    fake = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "tests.upbit_gateway.fake_upstream:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(fake_port),
        ],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    environment.update(
        {
            "PYTHONPATH": "apps/upbit_gateway",
            "UPBIT_GATEWAY_BASE_URL": f"http://127.0.0.1:{fake_port}",
            "UPBIT_GATEWAY_ALLOW_LOOPBACK_TEST": "true",
            "UPBIT_GATEWAY_WEBSOCKET_PUBLIC_URL": f"ws://127.0.0.1:{fake_port}/websocket/public",
            "UPBIT_GATEWAY_WEBSOCKET_PRIVATE_URL": f"ws://127.0.0.1:{fake_port}/websocket/private",
            "UPBIT_ACCESS_KEY": "fake-e2e-access",
            "UPBIT_SECRET_KEY": FAKE_SECRET_KEY,
            "UPBIT_GATEWAY_OPERATOR_TOKEN": E2E_OPERATOR_TOKEN,
        }
    )
    gateway = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "goodmoneying_upbit_gateway.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(gateway_port),
        ],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        fake_url = f"http://127.0.0.1:{fake_port}"
        gateway_url = f"http://127.0.0.1:{gateway_port}"
        _wait_ready(f"{fake_url}/__calls", fake)
        _wait_ready(f"{gateway_url}/health", gateway)
        yield fake_url, gateway_url, fake, gateway
    finally:
        for process in (gateway, fake):
            process.terminate()
        for process in (gateway, fake):
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def _process_output(process: subprocess.Popen[str]) -> str:
    stdout = process.stdout.read() if process.stdout is not None else ""
    stderr = process.stderr.read() if process.stderr is not None else ""
    return stdout + stderr


def _execute(base_url: str, endpoint_id: str, parameters: dict[str, object]) -> httpx.Response:
    return httpx.post(
        f"{base_url}/v1/requests",
        json={"endpoint_id": endpoint_id, "parameters": parameters},
        headers={"Origin": "https://browser.example"},
        timeout=3,
    )


def test_actual_gateway_process_uses_canonical_authenticated_query() -> None:
    with _processes() as (fake_url, gateway_url, _, _):
        invalid = _execute(
            gateway_url,
            "rest.get-pocket-api-keys",
            {"uuids[]": [{"nested": "value"}]},
        )
        calls_after_invalid = httpx.get(f"{fake_url}/__calls").json()
        response = _execute(
            gateway_url,
            "rest.get-pocket-api-keys",
            {
                "uuids[]": [
                    "2026-07-16T03:00:00+09:00",
                    "id&uuid=extra",
                    "#fragment",
                ],
                "include_expired": True,
            },
        )

    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "INVALID_PARAMETERS"
    assert calls_after_invalid == []
    assert response.status_code == 200
    assert response.json()["response"]["body"]["raw_query"] == (
        "uuids[]=2026-07-16T03%3A00%3A00%2B09%3A00"
        "&uuids[]=id%26uuid%3Dextra&uuids[]=%23fragment&include_expired=true"
    )
    assert response.json()["response"]["body"]["decoded_query"] == [
        ["uuids[]", "2026-07-16T03:00:00+09:00"],
        ["uuids[]", "id&uuid=extra"],
        ["uuids[]", "#fragment"],
        ["include_expired", "true"],
    ]


def test_actual_gateway_process_against_fake_upstream_end_to_end() -> None:
    with _processes() as (fake_url, gateway_url, fake, gateway):
        public = _execute(gateway_url, "rest.list-trading-pairs", {})
        origin_started_at = time.monotonic()
        origin_responses = [_execute(gateway_url, "rest.list-trading-pairs", {}) for _ in range(2)]
        origin_elapsed = time.monotonic() - origin_started_at
        authenticated_read = _execute(gateway_url, "rest.get-pocket-information", {})
        unauthorized = _execute(gateway_url, "rest.get-balance", {})
        websocket = _execute(gateway_url, "websocket.ticker", {})
        order_statuses = [
            _execute(
                gateway_url,
                "rest.order-test",
                {"side": "bid", "market": "KRW-BTC", "price": str(status), "ord_type": "price"},
            ).status_code
            for status in (1000, 400, 429, 418)
        ]
        cooldown_started_at = time.monotonic()
        after_cooldown = _execute(
            gateway_url,
            "rest.order-test",
            {"side": "bid", "market": "KRW-BTC", "price": "400", "ord_type": "price"},
        )
        cooldown_elapsed = time.monotonic() - cooldown_started_at
        blocked = _execute(gateway_url, "rest.new-order", {})
        upstream_calls = httpx.get(f"{fake_url}/__calls").json()

    process_output = _process_output(fake) + _process_output(gateway)
    assert public.status_code == 200
    assert [response.status_code for response in origin_responses] == [200, 200]
    assert origin_elapsed < 1
    assert authenticated_read.status_code == 200
    assert unauthorized.status_code == 401
    assert websocket.status_code == 422
    assert order_statuses == [201, 400, 429, 418]
    assert after_cooldown.status_code == 400
    assert cooldown_elapsed >= 0.9
    assert blocked.status_code == 403
    assert all(call["origin"] is None for call in upstream_calls)
    assert not any(call["path"] == "/v1/orders" for call in upstream_calls)
    authorizations = [
        call["authorization"] for call in upstream_calls if call["authorization"] is not None
    ]
    assert authorizations
    tokens = [authorization.removeprefix("Bearer ") for authorization in authorizations]
    combined = "".join(
        response.text
        for response in (
            public,
            *origin_responses,
            authenticated_read,
            unauthorized,
            websocket,
            after_cooldown,
            blocked,
        )
    )
    for sensitive in ("fake-e2e-access", FAKE_SECRET_KEY, *authorizations, *tokens):
        assert sensitive not in combined
        assert sensitive not in process_output
    assert "Bearer " not in process_output


def test_actual_gateway_and_fake_upbit_websocket_process_end_to_end() -> None:
    from websockets.sync.client import connect

    with _processes() as (fake_url, gateway_url, fake, gateway):
        websocket_url = gateway_url.replace("http://", "ws://") + "/v1/websocket"
        headers = {"X-Operator-Token": E2E_OPERATOR_TOKEN}
        origin = Origin(gateway_url)
        with connect(websocket_url, origin=origin, additional_headers=headers) as public:
            public.send(
                '{"action":"connect","request_id":"c","visibility":"public",'
                '"ticket":"ticket-public","format":"JSON_LIST"}'
            )
            connected = json.loads(public.recv())
            public.send(
                '{"action":"subscribe","request_id":"s","endpoint_id":"websocket.ticker",'
                '"parameters":{"codes":["KRW-BTC"],"is_only_realtime":true}}'
            )
            subscribed = json.loads(public.recv())
            frame = json.loads(public.recv())
            public.send('{"action":"list","request_id":"l"}')
            listed = [json.loads(public.recv()), json.loads(public.recv())]

        with connect(websocket_url, origin=origin, additional_headers=headers) as private:
            private.send(
                '{"action":"connect","request_id":"pc","visibility":"private",'
                '"ticket":"ticket-private","format":"DEFAULT"}'
            )
            private_connected = json.loads(private.recv())
            private.send(
                '{"action":"subscribe","request_id":"ps","endpoint_id":"websocket.my-asset",'
                '"parameters":{}}'
            )
            private_subscribed = json.loads(private.recv())
            private.send('{"action":"list","request_id":"pl"}')
            private_listed = [json.loads(private.recv()), json.loads(private.recv())]

        calls = httpx.get(f"{fake_url}/__calls").json()

    process_output = _process_output(fake) + _process_output(gateway)
    assert (connected["state"], subscribed["action"]) == ("connected", "subscribed")
    assert frame["event"] == "frame"
    assert frame["binary"] is True
    assert frame["payload"][0]["code"] == "KRW-BTC"
    assert {item["event"] for item in listed} == {"subscription", "frame"}
    public_list_frame = next(item for item in listed if item["event"] == "frame")
    assert public_list_frame["payload"]["result"] == [
        {"type": "ticker", "codes": ["KRW-BTC"], "is_only_realtime": True}
    ]
    assert (private_connected["state"], private_subscribed["action"]) == (
        "connected",
        "subscribed",
    )
    assert {item["event"] for item in private_listed} == {"subscription", "frame"}
    private_frames = [item for item in private_listed if item["event"] == "frame"]
    assert private_frames[0]["payload"]["method"] == "LIST_SUBSCRIPTIONS"
    assert private_frames[0]["payload"]["result"] == [{"type": "myAsset"}]
    websocket_connections = [call for call in calls if call["method"] == "WEBSOCKET"]
    assert websocket_connections[0]["origin"] is None
    assert websocket_connections[0]["authorization"] is None
    assert websocket_connections[1]["authorization"].startswith("Bearer ")
    rendered = json.dumps(
        [
            connected,
            subscribed,
            frame,
            listed,
            private_connected,
            private_subscribed,
            private_listed,
        ]
    )
    authorization = websocket_connections[1]["authorization"]
    for secret in ("fake-e2e-access", FAKE_SECRET_KEY, authorization, authorization[7:]):
        assert secret not in rendered
        assert secret not in process_output


def test_actual_websocket_malformed_error_reconnect_and_message_rate_limit() -> None:
    from websockets.sync.client import connect

    with _processes() as (_, gateway_url, _, _):
        websocket_url = gateway_url.replace("http://", "ws://") + "/v1/websocket"
        with connect(
            websocket_url,
            origin=Origin(gateway_url),
            additional_headers={"X-Operator-Token": E2E_OPERATOR_TOKEN},
        ) as websocket:
            websocket.send(
                '{"action":"connect","request_id":"c","visibility":"public",'
                '"ticket":"ticket","format":"DEFAULT"}'
            )
            assert json.loads(websocket.recv())["state"] == "connected"
            for request_id, code in (("m", "KRW-MALFORMED"), ("e", "KRW-ERROR")):
                websocket.send(
                    json.dumps(
                        {
                            "action": "subscribe",
                            "request_id": request_id,
                            "endpoint_id": "websocket.ticker",
                            "parameters": {"codes": [code]},
                        }
                    )
                )
                events = [json.loads(websocket.recv()), json.loads(websocket.recv())]
                assert {event["event"] for event in events} == {"subscription", "error"}
            websocket.send(
                '{"action":"subscribe","request_id":"r","endpoint_id":"websocket.ticker",'
                '"parameters":{"codes":["KRW-RECONNECT"]}}'
            )
            assert json.loads(websocket.recv())["action"] == "subscribed"
            reconnecting = json.loads(websocket.recv())
            reconnected = json.loads(websocket.recv())
            assert (reconnecting["state"], reconnected["state"]) == (
                "reconnecting",
                "connected",
            )
            started_at = time.monotonic()
            for index in range(6):
                websocket.send(json.dumps({"action": "list", "request_id": f"l{index}"}))
            events = [json.loads(websocket.recv()) for _ in range(12)]
            elapsed = time.monotonic() - started_at

    assert elapsed >= 0.7
    assert sum(event["event"] == "subscription" for event in events) == 6
    assert sum(event["event"] == "frame" for event in events) == 6
