from __future__ import annotations

from pathlib import Path

MIGRATION = Path("docs/contracts/db/migrations/20260718000500_p5_portfolio_bot_risk.sql")
DOMAIN = Path("docs/02_Architecture/system-trading-domain.md")
PRODUCT = Path("docs/01_Product.md")


def test_P5_1_DB는_포트폴리오_봇_주문_위험_기본_테이블을_정의한다() -> None:
    sql = MIGRATION.read_text()

    for table in (
        "portfolios",
        "portfolio_policies",
        "capital_allocations",
        "bot_definitions",
        "bot_instances",
        "bot_state_transitions",
        "order_intents",
        "exchange_orders",
        "order_fills",
        "position_projections",
        "risk_limits",
        "risk_events",
        "kill_switches",
    ):
        assert f"CREATE TABLE {table}" in sql

    assert "REFERENCES strategy_versions(id) ON DELETE RESTRICT" in sql
    assert "REFERENCES backtest_runs(id) ON DELETE RESTRICT" in sql
    assert "REFERENCES instruments(id)" in sql
    assert "UNIQUE (owner_id, name)" in sql
    assert "UNIQUE (bot_instance_id, idempotency_key)" in sql


def test_P5_1_DB는_paper_shadow_경계와_live_기본거부를_강제한다() -> None:
    sql = MIGRATION.read_text()

    assert "CHECK (execution_mode IN ('paper','shadow'))" in sql
    assert (
        "CHECK (stage IN ('draft','backtest','paper','shadow','paused','stopped','faulted'))"
        in sql
    )
    assert "'live'" not in _check_lines(sql)
    assert "outcome_unknown" in sql
    assert "risk_rejected" in sql
    assert "kill_switch_rejected" in sql
    assert "open_order_policy IN ('leave_open','cancel_open')" in sql


def test_P5_1_DB는_감사_이벤트와_위험_증거를_append_only로_보호한다() -> None:
    sql = MIGRATION.read_text()

    assert "reject_p5_append_only_mutation" in sql
    for trigger in (
        "bot_state_transitions_append_only_update",
        "order_fills_append_only_delete",
        "risk_events_append_only_update",
        "kill_switches_append_only_delete",
    ):
        assert trigger in sql

    assert "risk_policy_version" in sql
    assert "decision_input_hash" in sql
    assert "evidence JSONB NOT NULL DEFAULT '{}'::jsonb" in sql
    assert "CHECK (jsonb_typeof(evidence) = 'object')" in sql


def test_P5_1_제품과_도메인_문서는_P5_범위를_live_이전으로_분리한다() -> None:
    product = PRODUCT.read_text()
    domain = DOMAIN.read_text()

    assert "P5 | 전략을 안전한 모의 운영에 연결한다" in product
    assert "paper·shadow" in product
    assert "실거래 활성화는 기본 차단" in product
    assert "paper`, `shadow`, 자동 테스트 actor는 실제 주문 명령을 제출할 수 없다" in domain


def _check_lines(sql: str) -> str:
    return "\n".join(line for line in sql.splitlines() if "CHECK" in line)
