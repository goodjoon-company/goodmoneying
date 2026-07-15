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
def _processes() -> Iterator[tuple[str, str]]:
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
        yield fake_url, gateway_url
    finally:
        for process in (gateway, fake):
            process.terminate()
        for process in (gateway, fake):
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


def _execute(base_url: str, endpoint_id: str, parameters: dict[str, object]) -> httpx.Response:
    return httpx.post(
        f"{base_url}/v1/requests",
        json={"endpoint_id": endpoint_id, "parameters": parameters},
        headers={"Origin": "https://browser.example"},
        timeout=3,
    )


def test_actual_gateway_process_against_fake_upstream_end_to_end() -> None:
    with _processes() as (fake_url, gateway_url):
        public = _execute(gateway_url, "rest.list-trading-pairs", {})
        authenticated_read = _execute(gateway_url, "rest.get-pocket-information", {})
        unauthorized = _execute(gateway_url, "rest.get-balance", {})
        order_statuses = [
            _execute(
                gateway_url,
                "rest.order-test",
                {"side": "bid", "market": "KRW-BTC", "price": str(status), "ord_type": "price"},
            ).status_code
            for status in (1000, 400, 429, 418)
        ]
        blocked = _execute(gateway_url, "rest.new-order", {})
        upstream_calls = httpx.get(f"{fake_url}/__calls").json()

    assert public.status_code == 200
    assert authenticated_read.status_code == 200
    assert unauthorized.status_code == 401
    assert order_statuses == [201, 400, 429, 418]
    assert blocked.status_code == 403
    assert all(call["origin"] is None for call in upstream_calls)
    assert not any(call["path"] == "/v1/orders" for call in upstream_calls)
    combined = "".join(
        response.text for response in (public, authenticated_read, unauthorized, blocked)
    )
    assert "fake-e2e-access" not in combined
    assert FAKE_SECRET_KEY not in combined
