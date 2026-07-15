from __future__ import annotations

import json
from pathlib import Path

import httpx

from goodmoneying_upbit_gateway.live_verification import parse_key_file, run_safe_verification


def test_parse_key_file_accepts_labeled_values_without_returning_labels(tmp_path: Path) -> None:
    key_file = tmp_path / "upbit-key.txt"
    key_file.write_text(
        "Access Key: fake-access-value\nSecret Key=fake-secret-value\n",
        encoding="utf-8",
    )

    credentials = parse_key_file(key_file)

    assert credentials.access_key == "fake-access-value"
    assert credentials.secret_key == "fake-secret-value"


def test_safe_verification_calls_only_public_read_authenticated_read_and_order_test(
    tmp_path: Path,
) -> None:
    key_file = tmp_path / "upbit-key.txt"
    access_key = "live-safe-fake-access"
    secret_key = "s" * 64
    key_file.write_text(
        f"access_key={access_key}\nsecret_key={secret_key}\n",
        encoding="utf-8",
    )
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/v1/market/all":
            return httpx.Response(
                200,
                json=[{"market": "KRW-BTC"}],
                headers={"Remaining-Req": "group=market; min=600; sec=9"},
            )
        if request.url.path == "/v1/accounts":
            return httpx.Response(
                401,
                json={"error": {"name": "no_authorization_ip"}},
                headers={"Remaining-Req": "group=default; min=1800; sec=29"},
            )
        if request.url.path == "/v1/orders/test":
            return httpx.Response(
                400,
                json={"error": {"name": "under_min_total_bid"}},
                headers={"Remaining-Req": "group=order-test; min=480; sec=7"},
            )
        raise AssertionError(f"허용하지 않은 상향 호출: {request.method} {request.url.path}")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = run_safe_verification(key_file, http_client=client, base_url="https://api.upbit.com")

    assert [(request.method, request.url.path) for request in calls] == [
        ("GET", "/v1/market/all"),
        ("GET", "/v1/accounts"),
        ("POST", "/v1/orders/test"),
    ]
    assert report["inventory"] == {
        "rest_total": 51,
        "websocket_streams": 14,
        "blocked_rest": 14,
    }
    assert report["blocked_verification"] == {
        "catalog_count": 14,
        "locally_blocked": 14,
        "upstream_calls": 0,
    }
    assert [item["status_code"] for item in report["allowed_results"]] == [200, 401, 400]
    assert [item.get("error_name") for item in report["allowed_results"]] == [
        None,
        "no_authorization_ip",
        "under_min_total_bid",
    ]
    rendered = json.dumps(report, ensure_ascii=False)
    assert access_key not in rendered
    assert secret_key not in rendered
    assert "Authorization" not in rendered
    assert "Bearer" not in rendered
