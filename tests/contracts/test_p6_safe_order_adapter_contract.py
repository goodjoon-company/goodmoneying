from __future__ import annotations

from pathlib import Path

MIGRATION = Path("docs/contracts/db/migrations/20260718001100_p6_safe_order_outbox.sql")
DOMAIN = Path("docs/02_Architecture/system-trading-domain.md")
TASK = Path("docs/Task/P6.md")
MODULE = Path("packages/shared/goodmoneying_shared/upbit_safe_order_adapter.py")


def test_P6_6_DB는_권한_attestation과_주문_outbox를_안전_계약으로_정의한다() -> None:
    sql = MIGRATION.read_text()

    assert "CREATE TABLE upbit_api_key_permission_attestations" in sql
    assert "CREATE TABLE upbit_order_outbox" in sql
    assert "CHECK (has_order_permission IS TRUE)" in sql
    assert "CHECK (has_order_read_permission IS TRUE)" in sql
    assert "CHECK (has_withdraw_permission IS FALSE)" in sql
    assert "CHECK (actor_id !~* '^(ci|ai|service):')" in sql
    assert "CHECK (status IN ('ready','blocked'))" in sql
    assert "submit_attempt_count INTEGER NOT NULL DEFAULT 0" in sql
    assert "CHECK (submit_attempt_count = 0)" in sql
    assert "CREATE FUNCTION validate_p6_upbit_order_outbox_consistency()" in sql
    assert "intent.status AS order_intent_status" in sql
    assert "ready outbox requires approved order intent" in sql
    assert "NEW.permission_attestation_id IS NOT NULL" in sql
    assert "upbit_order_outbox_validate_consistency" in sql
    assert "upbit_order_outbox_append_only_update" in sql
    assert "upbit_api_key_permission_attestations_append_only_update" in sql


def test_P6_6_문서와_adapter는_출금_권한과_실제_제출을_금지한다() -> None:
    domain = DOMAIN.read_text()
    task = TASK.read_text()
    source = MODULE.read_text()

    assert "P6-6 안전 주문 adapter" in task
    assert "출금 권한" in task
    assert "outbox" in domain
    assert "withdraw_permission_present" in source
    assert "can_submit=False" in source
    assert "POST /v1/orders" not in source
