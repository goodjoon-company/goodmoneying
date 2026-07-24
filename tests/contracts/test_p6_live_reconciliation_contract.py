from __future__ import annotations

from pathlib import Path

MIGRATION = Path(
    "docs/contracts/db/migrations/20260718001400_p6_live_reconciliation_application.sql"
)
CONTRACT = Path("docs/contracts/upbit/live-order-reconciliation.md")
ADAPTER = Path("packages/shared/goodmoneying_shared/upbit_live_reconciliation.py")
STORE = Path("packages/shared/goodmoneying_shared/portfolio_bot_store.py")


def test_P6_9_DB는_live_binding과_REST_대사_run이_일치할_때만_application을_허용한다() -> None:
    sql = MIGRATION.read_text()

    assert "CREATE TABLE upbit_live_reconciliation_applications" in sql
    assert (
        "live_exchange_order_binding_id BIGINT NOT NULL "
        "REFERENCES upbit_live_exchange_order_bindings"
    ) in sql
    assert "reconciliation_run_id BIGINT NOT NULL REFERENCES reconciliation_runs" in sql
    assert "CHECK (source = 'rest_order_snapshot')" in sql
    assert "CHECK (source_endpoint IN (" in sql
    assert "CHECK (observed_state IN ('done','cancel','prevented','rejected'))" in sql
    assert "CHECK (can_resubmit IS FALSE)" in sql
    assert "CHECK (actual_request_sent IS FALSE)" in sql
    assert "CHECK (actual_order_cancel_sent IS FALSE)" in sql
    assert "validate_p6_live_reconciliation_application()" in sql
    assert "live reconciliation application requires matching binding" in sql
    assert "live reconciliation application requires succeeded reconciliation run" in sql
    assert "live reconciliation application snapshot must match reconciliation evidence" in sql
    assert "reconciliation_runs_require_live_application" in sql
    assert "live succeeded reconciliation run requires live application" in sql
    assert "upbit_live_reconciliation_applications_append_only_update" in sql


def test_P6_9_계약과_adapter는_실제_HTTP_주문_제출_취소_private_WS를_구현하지_않는다() -> None:
    contract = CONTRACT.read_text()
    adapter = ADAPTER.read_text()
    store = STORE.read_text()

    assert "live 주문 대사(reconciliation) 적용" in contract
    assert "실제 REST 호출을 만들지 않는다" in contract
    assert "주문 제출을 하지 않는다" in contract
    assert "주문 취소를 하지 않는다" in contract
    assert "private WebSocket 연결을 열지 않는다" in contract
    assert "can_resubmit=false" in contract
    for forbidden in (
        "import requests",
        "import httpx",
        "aiohttp",
        "urlopen",
        ".post(",
        ".delete(",
        "websockets.connect",
    ):
        assert forbidden not in adapter
        assert forbidden not in store
