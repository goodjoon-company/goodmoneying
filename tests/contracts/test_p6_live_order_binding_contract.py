from __future__ import annotations

from pathlib import Path

MIGRATION = Path("docs/contracts/db/migrations/20260718001200_p6_live_order_binding.sql")
CONTRACT = Path("docs/contracts/upbit/live-order-binding.md")
TASK = Path("docs/Task/P6.md")
MODULE = Path("packages/shared/goodmoneying_shared/upbit_live_order_binding.py")


def test_P6_7_DB는_live_exchange_order_binding_계약을_정의한다() -> None:
    sql = MIGRATION.read_text()

    assert "ALTER TABLE exchange_orders" in sql
    assert "'paper','shadow','live'" in sql
    assert "CREATE TABLE upbit_live_exchange_order_bindings" in sql
    assert "exchange_order_id BIGINT NOT NULL REFERENCES exchange_orders" in sql
    assert "live_order_identifier_id BIGINT NOT NULL REFERENCES live_order_identifiers" in sql
    assert "upbit_order_outbox_id BIGINT NOT NULL REFERENCES upbit_order_outbox" in sql
    assert "UNIQUE (exchange_account_id, upbit_order_uuid)" in sql
    assert "UNIQUE (exchange_account_id, upbit_identifier)" in sql
    assert "CHECK (upbit_order_uuid ~" in sql
    assert "CHECK (upbit_identifier ~ '^gm1_[a-z2-7]{52}$')" in sql
    assert "FROM upbit_order_test_runs test_run" in sql
    assert "order-test response identifier cannot be bound as live exchange order" in sql
    assert "live binding requires reserved live identifier" in sql
    assert "live exchange order binding requires live exchange order" in sql
    assert "live exchange order no longer matches Upbit live binding" in sql
    assert "CREATE CONSTRAINT TRIGGER exchange_orders_require_live_binding" in sql
    assert "DEFERRABLE INITIALLY DEFERRED" in sql
    assert "upbit_live_exchange_order_bindings_validate" in sql
    assert "upbit_live_exchange_order_bindings_append_only_update" in sql


def test_P6_7_문서와_adapter는_실제_제출을_추가하지_않고_재제출을_금지한다() -> None:
    contract = CONTRACT.read_text()
    task = TASK.read_text()
    source = MODULE.read_text()

    assert "P6-7 live 주문 결합" in task
    assert "POST /v1/orders 호출 없음" in contract
    assert "order_submit_response" in contract
    assert "can_submit=False" in source
    assert "can_cancel=False" in source
    assert "can_resubmit=False" in source
    assert "POST /v1/orders" not in source
