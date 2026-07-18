from __future__ import annotations

from pathlib import Path

MIGRATION = Path("docs/contracts/db/migrations/20260718000200_p4_backtest_runs.sql")
DB_README = Path("docs/contracts/db/README.md")
DOMAIN = Path("docs/02_Architecture/system-trading-domain.md")
STORE = Path("packages/shared/goodmoneying_shared/backtest_store.py")


def test_P4_2_migration은_백테스트_결과_테이블과_불변_제약을_선언한다() -> None:
    sql = MIGRATION.read_text()

    for table in (
        "backtest_runs",
        "backtest_trades",
        "backtest_equity_points",
        "backtest_metrics",
        "backtest_artifacts",
    ):
        assert f"CREATE TABLE {table}" in sql

    assert "UNIQUE (input_hash)" in sql
    assert (
        "UNIQUE (strategy_version_id, dataset_version_id, engine_version, parameter_hash, seed)"
        in sql
    )
    assert "FOREIGN KEY (strategy_version_id, strategy_graph_hash)" in sql
    assert "FOREIGN KEY (dataset_version_id, dataset_content_hash)" in sql
    assert "reject_backtest_result_mutation" in sql
    assert "append-only" in sql


def test_P4_2_migration은_체결_성과_산출물의_자연키를_강제한다() -> None:
    sql = MIGRATION.read_text()

    assert "UNIQUE (run_id, trade_sequence)" in sql
    assert "UNIQUE (run_id, occurred_at)" in sql
    assert "UNIQUE (run_id, metric_name, scope_key)" in sql
    assert "UNIQUE (run_id, artifact_type, content_hash)" in sql
    assert "CHECK (filled_quantity >= 0)" in sql
    assert "CHECK (remaining_quantity >= 0)" in sql
    assert "CHECK (metric_value IS NOT NULL OR metric_payload <> '{}'::jsonb)" in sql


def test_P4_2_DB_계약과_도메인_문서는_영속화_범위를_연결한다() -> None:
    db_readme = DB_README.read_text()
    domain = DOMAIN.read_text()

    assert "20260718000200_p4_backtest_runs.sql" in db_readme
    assert "backtest_runs" in domain
    assert "input_hash" in domain
    assert "result_hash" in domain
    assert "Backtest Store" in domain


def test_P4_4_Backtest_Store는_목록_cursor_무결성과_상한을_계약으로_고정한다() -> None:
    source = STORE.read_text()

    assert "class BacktestCursorMismatchError" in source
    assert "backtest-run-list-v1" in source
    assert "def list_runs(" in source
    assert "COALESCE(MAX(id), 0)" in source
    assert "ORDER BY id DESC" in source
    assert "LIMIT %s" in source
    assert "ceiling" in source
    assert "lastId" in source
    assert "digest" in source
