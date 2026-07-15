from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest

from goodmoneying_upbit_gateway.live_verification import parse_key_file, run_safe_verification

_CLI_PATH = Path(__file__).parents[2] / "scripts" / "verify_upbit_safe_live.py"
_CLI_SPEC = importlib.util.spec_from_file_location("verify_upbit_safe_live", _CLI_PATH)
assert _CLI_SPEC is not None and _CLI_SPEC.loader is not None
verify_upbit_safe_live = importlib.util.module_from_spec(_CLI_SPEC)
_CLI_SPEC.loader.exec_module(verify_upbit_safe_live)


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


def _safe_verification_report(
    *,
    public: tuple[int | None, str | None] = (200, None),
    authenticated: tuple[int | None, str | None] = (200, None),
    order_test: tuple[int | None, str | None] = (201, None),
) -> dict[str, object]:
    def result(
        endpoint_id: str, outcome: tuple[int | None, str | None]
    ) -> dict[str, object]:
        status_code, error_name = outcome
        item: dict[str, object] = {
            "endpoint_id": endpoint_id,
            "status_code": status_code,
        }
        if error_name is not None:
            item["error_name"] = error_name
        return item

    return {
        "allowed_results": [
            result("rest.list-trading-pairs", public),
            result("rest.get-balance", authenticated),
            result("rest.order-test", order_test),
        ],
        "blocked_verification": {
            "catalog_count": 14,
            "locally_blocked": 14,
            "upstream_calls": 0,
        },
    }


@pytest.mark.parametrize(
    ("authenticated", "order_test"),
    [
        ((200, None), (201, None)),
        ((401, "no_authorization_ip"), (400, "under_min_total_bid")),
        ((401, "out_of_scope"), (401, "no_authorization_ip")),
    ],
)
def test_cli_returns_zero_only_for_explicitly_allowed_safe_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    authenticated: tuple[int | None, str | None],
    order_test: tuple[int | None, str | None],
) -> None:
    monkeypatch.setattr(
        verify_upbit_safe_live,
        "run_safe_verification",
        lambda *_args, **_kwargs: _safe_verification_report(
            authenticated=authenticated,
            order_test=order_test,
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["verify_upbit_safe_live.py", "--key-file", str(tmp_path / "unused-key.txt")],
    )

    assert verify_upbit_safe_live.main() == 0


@pytest.mark.parametrize(
    ("public", "authenticated", "order_test"),
    [
        ((None, "UpstreamConnectionError"), (200, None), (201, None)),
        ((200, None), (None, "UpstreamProtocolError"), (201, None)),
        ((200, None), (200, None), (None, "UpstreamTimeout")),
        ((200, None), (403, "no_authorization_ip"), (201, None)),
        ((200, None), (401, "invalid_access_key"), (201, None)),
        ((200, None), (200, None), (400, "validation_error")),
        ((200, None), (200, None), (200, None)),
    ],
)
def test_cli_returns_nonzero_for_transport_or_unexpected_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    public: tuple[int | None, str | None],
    authenticated: tuple[int | None, str | None],
    order_test: tuple[int | None, str | None],
) -> None:
    monkeypatch.setattr(
        verify_upbit_safe_live,
        "run_safe_verification",
        lambda *_args, **_kwargs: _safe_verification_report(
            public=public,
            authenticated=authenticated,
            order_test=order_test,
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["verify_upbit_safe_live.py", "--key-file", str(tmp_path / "unused-key.txt")],
    )

    assert verify_upbit_safe_live.main() == 1


def test_cli_rejects_blocked_inventory_drift_even_when_local_counts_match(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    report = _safe_verification_report()
    report["blocked_verification"] = {
        "catalog_count": 13,
        "locally_blocked": 13,
        "upstream_calls": 0,
    }
    monkeypatch.setattr(
        verify_upbit_safe_live,
        "run_safe_verification",
        lambda *_args, **_kwargs: report,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["verify_upbit_safe_live.py", "--key-file", str(tmp_path / "unused-key.txt")],
    )

    assert verify_upbit_safe_live.main() == 1
