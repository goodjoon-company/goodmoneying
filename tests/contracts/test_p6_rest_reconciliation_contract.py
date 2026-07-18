from __future__ import annotations

from pathlib import Path

CONTRACT = Path("docs/contracts/upbit/rest-order-reconciliation.md")
DOMAIN = Path("docs/02_Architecture/system-trading-domain.md")
TASK = Path("docs/Task/P6.md")
MODULE = Path("packages/shared/goodmoneying_shared/upbit_rest_reconciliation.py")


def test_P6_5_REST_snapshot_계약은_주문조회_권한과_재주문_금지를_명시한다() -> None:
    contract = CONTRACT.read_text()
    domain = DOMAIN.read_text()
    task = TASK.read_text()

    assert "주문조회" in contract
    assert "GET /v1/order" in contract
    assert "GET /v1/orders/open" in contract
    assert "GET /v1/orders/closed" in contract
    assert "GET /v1/orders/uuids" in contract
    assert "재주문하지 않는다" in contract
    assert "terminal snapshot" in contract
    assert "P6-5 private myOrder REST snapshot 적용" in task
    assert "REST snapshot" in domain


def test_P6_5_REST_snapshot_adapter는_네트워크_호출과_live_mode_확장을_포함하지_않는다() -> None:
    source = MODULE.read_text()

    assert "parse_upbit_rest_order_snapshot" in source
    assert "apply_upbit_rest_order_snapshot" in source
    assert "reconcile_exchange_order" in source
    assert "requests." not in source
    assert "httpx" not in source
    assert "aiohttp" not in source
    assert "\"live\"" not in source
