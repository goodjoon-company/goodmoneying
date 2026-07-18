from __future__ import annotations

from pathlib import Path

MIGRATION = Path("docs/contracts/db/migrations/20260718000900_p6_order_identity_separation.sql")
DOMAIN = Path("docs/02_Architecture/system-trading-domain.md")
TASK = Path("docs/Task/P6.md")


def test_P6_2_DB는_order_test_증적과_live_identifier를_분리한다() -> None:
    sql = MIGRATION.read_text()

    for table in (
        "exchange_accounts",
        "upbit_order_identifier_reservations",
        "live_order_identifiers",
        "upbit_order_test_runs",
    ):
        assert f"CREATE TABLE {table}" in sql

    assert "REFERENCES order_intents(id) ON DELETE RESTRICT" in sql
    assert "UNIQUE (exchange_account_id, identifier)" in sql
    assert "UNIQUE (order_intent_id)" in sql
    assert "CHECK (identifier ~ '^gm1_[a-z2-7]{52}$')" in sql
    assert "lookup_allowed BOOLEAN NOT NULL DEFAULT FALSE" in sql
    assert "cancel_allowed BOOLEAN NOT NULL DEFAULT FALSE" in sql
    assert "CHECK (lookup_allowed = FALSE)" in sql
    assert "CHECK (cancel_allowed = FALSE)" in sql
    assert "response_uuid TEXT" in sql
    assert "response_identifier TEXT" in sql
    assert "jsonb_typeof(request_parameters) = 'object'" in sql
    assert "jsonb_typeof(response_body) = 'object'" in sql
    assert "CREATE FUNCTION p6_upbit_live_order_identifier" in sql
    assert "CREATE FUNCTION reserve_p6_upbit_order_identifier" in sql
    assert "CREATE FUNCTION validate_p6_live_order_identifier()" in sql
    assert "CREATE FUNCTION reserve_p6_live_order_identifier()" in sql
    assert "CREATE FUNCTION validate_p6_order_test_identifier_not_live()" in sql
    assert "CREATE FUNCTION reserve_p6_order_test_identifier()" in sql
    assert "CREATE FUNCTION reject_p6_order_test_run_mutation()" in sql
    assert "CREATE TRIGGER live_order_identifiers_validate_identity" in sql
    assert "CREATE TRIGGER upbit_order_test_runs_reject_live_identifier" in sql
    assert "CREATE TRIGGER live_order_identifiers_reserve_identifier" in sql
    assert "CREATE TRIGGER upbit_order_test_runs_reserve_identifiers" in sql
    assert "CREATE TRIGGER upbit_order_test_runs_reject_mutation" in sql
    assert "AFTER INSERT ON upbit_order_test_runs" in sql
    assert "BEFORE UPDATE OR DELETE ON upbit_order_test_runs" in sql
    assert "UNIQUE (exchange_account_id, identifier)" in sql
    assert "UNIQUE (source_table, source_column, source_id)" in sql
    assert "NEW.idempotency_key <> actual_idempotency_key" in sql
    assert "NEW.identifier <> expected_identifier" in sql
    assert "order-test response identifier cannot be reserved as a live order identifier" in sql
    assert "live order identifier cannot be recorded as an order-test response identifier" in sql
    assert "Upbit order identifier is already reserved for another source" in sql
    assert "Upbit order-test run evidence is append-only" in sql


def test_P6_2_문서는_order_test와_live_order_identity를_혼동하지_않는다() -> None:
    domain = DOMAIN.read_text()
    task = TASK.read_text()

    assert "주문 테스트 API가 반환한 식별자는 조회·취소에 쓸 수 없으므로" in domain
    assert "order-test 증적과 실제 주문 식별자 격리" in task
    assert "테스트 주문 응답을 실제 주문 원장·조회·취소 식별자로 재사용하지 못하게 한다" in task
