from __future__ import annotations

from pathlib import Path

STORE = Path("packages/shared/goodmoneying_shared/portfolio_bot_store.py")
WORKER = Path("apps/worker/goodmoneying_worker/risk_evaluation_worker.py")
DOMAIN = Path("docs/02_Architecture/system-trading-domain.md")
TASK = Path("docs/Task/P5.md")


def test_P5_4_Store는_created_주문을_잠그고_위험_판정을_기록한다() -> None:
    source = STORE.read_text()

    assert "def evaluate_next_order_intent_risk(" in source
    assert "intent.status='created'" in source
    assert "FOR UPDATE SKIP LOCKED" in source
    assert "risk_rejected" in source
    assert "policy_approved" in source
    assert "limit_rejected" in source
    assert "kill_switch_rejected" in source
    assert "INSERT INTO risk_events" in source


def test_P5_4_Store는_kill_switch와_paper_queue_연결을_보호한다() -> None:
    source = STORE.read_text()

    assert "kill_switches" in source
    assert "pg_advisory_xact_lock(hashtextextended(%s, 0))" in source
    assert "latest_switch.state='armed'" in source
    assert "INSERT INTO paper_execution_jobs" in source
    assert "ON CONFLICT (order_intent_id) DO NOTHING" in source
    assert "execution_mode='paper'" in source


def test_P5_4_kill_switch_차단은_paper_attempt_budget을_소모하지_않는다() -> None:
    source = STORE.read_text()

    assert "attempt_count=GREATEST(attempt_count - 1, 0)" in source
    assert "last_error_code='KILL_SWITCH_ARMED'" in source


def test_P5_4_위험평가는_실제_paper_fill과_같은_명목금액을_사용한다() -> None:
    source = STORE.read_text()

    assert "computed_notional = requested_quantity * limit_price" in source
    assert "requested_notional != computed_notional" in source
    assert "return None" in source


def test_P5_4_Worker와_문서는_실제_주문_없이_risk만_평가한다() -> None:
    worker = WORKER.read_text()
    domain = DOMAIN.read_text()
    task = TASK.read_text()

    assert "RiskEvaluationWorker" in worker
    assert "evaluate_next_order_intent_risk" in worker
    assert "P5-4" in domain
    assert "Risk Worker" in domain
    assert "실제 Upbit 주문" in task
