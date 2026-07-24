from __future__ import annotations

from pathlib import Path

MIGRATION = Path(
    "docs/contracts/db/migrations/20260718001300_p6_order_submit_rehearsal.sql"
)
CONTRACT = Path("docs/contracts/upbit/order-submit-rehearsal.md")
ADAPTER = Path("packages/shared/goodmoneying_shared/upbit_order_submit_rehearsal.py")


def test_P6_8_DB는_실제_주문_전_rehearsal_증적만_허용한다() -> None:
    sql = MIGRATION.read_text()

    assert "CREATE TABLE upbit_order_submit_rehearsals" in sql
    assert "upbit_order_outbox_id BIGINT NOT NULL REFERENCES upbit_order_outbox" in sql
    assert "endpoint_key TEXT NOT NULL" in sql
    assert "CHECK (endpoint_key = 'rest.new-order')" in sql
    assert "CHECK (http_method = 'POST')" in sql
    assert "CHECK (request_path = '/v1/orders')" in sql
    assert "CHECK (actual_request_sent IS FALSE)" in sql
    assert "CHECK (would_submit IS FALSE)" in sql
    assert "CHECK (can_bind_response IS FALSE)" in sql
    assert "CHECK (response_uuid IS NULL)" in sql
    assert "CHECK (response_identifier IS NULL)" in sql
    assert "validate_p6_order_submit_rehearsal()" in sql
    assert "order submit rehearsal requires reserved live identifier" in sql
    assert "order submit rehearsal cannot follow live binding" in sql
    assert "upbit_order_submit_rehearsals_append_only_update" in sql


def test_P6_8_계약과_adapter는_실제_HTTP_주문_제출을_구현하지_않는다() -> None:
    contract = CONTRACT.read_text()
    adapter = ADAPTER.read_text()

    assert "POST /v1/orders" in contract
    assert "실제 주문을 전송하지 않는다" in contract
    assert "actual_request_sent=false" in contract
    assert "would_submit=false" in contract
    assert "can_bind_response=false" in contract
    for forbidden in ("import requests", "import httpx", "aiohttp", "urlopen", ".post("):
        assert forbidden not in adapter
