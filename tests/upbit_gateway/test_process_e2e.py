from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
FAKE_SECRET_KEY = "e" * 64


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
def _processes() -> Iterator[
    tuple[str, str, subprocess.Popen[str], subprocess.Popen[str]]
]:
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
            "UPBIT_ACCESS_KEY": "fake-e2e-access",
            "UPBIT_SECRET_KEY": FAKE_SECRET_KEY,
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
        origin_responses = [
            _execute(gateway_url, "rest.list-trading-pairs", {}) for _ in range(2)
        ]
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
