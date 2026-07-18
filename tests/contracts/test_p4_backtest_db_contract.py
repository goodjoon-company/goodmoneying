from __future__ import annotations

from pathlib import Path

MIGRATION = Path("docs/contracts/db/migrations/20260718000200_p4_backtest_runs.sql")


def test_P4_2_DB는_백테스트_run과_결과_테이블을_정의한다() -> None:
    sql = MIGRATION.read_text()

    for table in (
        "backtest_runs",
        "backtest_trades",
        "backtest_equity_points",
        "backtest_metrics",
        "backtest_artifacts",
    ):
        assert f"CREATE TABLE {table}" in sql

    assert "REFERENCES strategy_versions(id) ON DELETE RESTRICT" in sql
    assert "REFERENCES dataset_versions(id) ON DELETE RESTRICT" in sql
    assert (
        "UNIQUE (strategy_version_id, dataset_version_id, engine_version, parameter_hash, seed)"
        in sql
    )
    assert "UNIQUE (idempotency_key)" in sql
    assert "CHECK (input_hash ~ '^[0-9a-f]{64}$')" in sql
    assert "CHECK (parameter_hash ~ '^[0-9a-f]{64}$')" in sql
    assert "CHECK (result_hash IS NULL OR result_hash ~ '^[0-9a-f]{64}$')" in sql


def test_P4_2_DB는_terminal_run과_결과를_불변으로_봉인한다() -> None:
    sql = MIGRATION.read_text()

    assert "enforce_backtest_run_terminal_seal" in sql
    assert "validate_backtest_run_inputs" in sql
    assert "reject_backtest_result_mutation" in sql
    assert "backtest_runs_terminal_update" in sql
    assert "backtest_trades_append_only_update" in sql
    assert "backtest_metrics_append_only_delete" in sql
    assert "strategy.status <> 'published'" in sql
    assert "version.sealed_at IS NULL" in sql
