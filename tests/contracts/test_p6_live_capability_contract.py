from __future__ import annotations

from pathlib import Path

MIGRATION = Path("docs/contracts/db/migrations/20260718001000_p6_live_capability_gate.sql")
DOMAIN = Path("docs/02_Architecture/system-trading-domain.md")
TASK = Path("docs/Task/P6.md")


def test_P6_3_DB는_live_capability를_폐쇄형_권위_상태로_정의한다() -> None:
    sql = MIGRATION.read_text()

    assert "CREATE TABLE trading_capabilities" in sql
    assert "CHECK (scope_type = 'global')" in sql
    assert "CHECK (scope_key = 'global')" in sql
    assert "CHECK (state IN ('live_disabled','live_enabled'))" in sql
    assert "CHECK (deployment_sha ~ '^[0-9a-f]{40}$')" in sql
    assert "CHECK (expires_at > approved_at)" in sql
    assert "CHECK (actor_id !~ '^(ci|ai|service):')" in sql
    assert "CREATE FUNCTION reject_p6_trading_capability_mutation()" in sql
    assert "CREATE TRIGGER trading_capabilities_append_only_update" in sql
    assert "CREATE TRIGGER trading_capabilities_append_only_delete" in sql
    assert "trading_capabilities_global_latest_idx" in sql


def test_P6_3_문서는_live_disabled_fail_closed를_명시한다() -> None:
    domain = DOMAIN.read_text()
    task = TASK.read_text()

    assert "조회 실패·불일치·만료·새 SHA는 `live_disabled`로 평가한다" in domain
    assert "live_disabled capability gate" in task
    assert "CI·AI·service actor는 live capability를 변경할 수 없다" in task
