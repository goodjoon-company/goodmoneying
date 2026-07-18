from __future__ import annotations

from pathlib import Path

MIGRATION = Path("docs/contracts/db/migrations/20260718000800_p5_reconciliation_runs.sql")
STORE = Path("packages/shared/goodmoneying_shared/portfolio_bot_store.py")
DOMAIN = Path("docs/02_Architecture/system-trading-domain.md")
TASK = Path("docs/Task/P5.md")


def test_P5_5_DB는_reconciliation_run_증적을_append_only로_정의한다() -> None:
    sql = MIGRATION.read_text()

    assert "CREATE TABLE reconciliation_runs" in sql
    assert (
        "exchange_order_id BIGINT NOT NULL REFERENCES exchange_orders(id) ON DELETE RESTRICT"
        in sql
    )
    assert "UNIQUE (exchange_order_id, run_key)" in sql
    assert "request_hash TEXT NOT NULL" in sql
    assert "status IN ('succeeded','mismatch','outcome_unknown')" in sql
    assert (
        "observed_status IN ('done','cancel','prevented','rejected','outcome_unknown','missing')"
        in sql
    )
    assert "reconciliation_runs_append_only_update" in sql
    assert "reconciliation_runs_append_only_delete" in sql


def test_P5_5_Store는_대사_fill과_position을_같은_transaction에서_갱신한다() -> None:
    source = STORE.read_text()

    assert "def reconcile_exchange_order(" in source
    assert "INSERT INTO reconciliation_runs" in source
    assert "fill_source, side" in source
    assert "'reconciliation'" in source
    assert "_upsert_position_projection(" in source
    assert "UPDATE exchange_orders" in source
    assert "UPDATE order_intents" in source
    assert "FOR UPDATE" in source


def test_P5_5_Store는_중복_run과_fill_mismatch를_차단한다() -> None:
    source = STORE.read_text()

    assert "ReconciliationIdempotencyConflictError" in source
    assert "request_hash" in source
    assert "_lock_reconciliation_run_key(" in source
    assert "reconciliation-run:" in source
    assert "reconciliation_mismatch" in source
    assert "ON CONFLICT DO NOTHING" in source
    assert "_same_fill(" in source


def test_P5_5_문서는_P5_대사를_실주문_없이_정의한다() -> None:
    domain = DOMAIN.read_text()
    task = TASK.read_text()

    assert "P5-5" in domain
    assert "reconciliation_runs" in domain
    assert "P5-5 reconciliation" in task
    assert "실제 Upbit 주문" in task
