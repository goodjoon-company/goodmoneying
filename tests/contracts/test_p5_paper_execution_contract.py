from __future__ import annotations

from pathlib import Path

MIGRATION = Path("docs/contracts/db/migrations/20260718000700_p5_paper_execution_jobs.sql")
STORE = Path("packages/shared/goodmoneying_shared/portfolio_bot_store.py")
WORKER = Path("apps/worker/goodmoneying_worker/paper_execution_worker.py")
DOMAIN = Path("docs/02_Architecture/system-trading-domain.md")


def test_P5_3_DB는_paper_execution_job_queue와_lease_fencing을_정의한다() -> None:
    sql = MIGRATION.read_text()

    assert "CREATE TABLE paper_execution_jobs" in sql
    assert "order_intent_id BIGINT NOT NULL REFERENCES order_intents(id) ON DELETE RESTRICT" in sql
    assert "UNIQUE (order_intent_id)" in sql
    for column in (
        "lease_owner TEXT",
        "lease_expires_at TIMESTAMPTZ",
        "lease_generation INTEGER NOT NULL DEFAULT 0",
        "attempt_count INTEGER NOT NULL DEFAULT 0",
        "max_attempts INTEGER NOT NULL DEFAULT 3",
        "next_retry_at TIMESTAMPTZ NOT NULL DEFAULT '1970-01-01T00:00:00Z'",
    ):
        assert column in sql
    assert "paper_execution_jobs_claim_idx" in sql
    assert "status IN ('pending','running','retry_wait','succeeded','dead_letter')" in sql


def test_P5_3_Store는_claim_complete_fail과_skip_locked를_구현한다() -> None:
    source = STORE.read_text()

    for function in (
        "def claim_next_paper_execution_job(",
        "def complete_claimed_paper_execution_job(",
        "def fail_claimed_paper_execution_job(",
    ):
        assert function in source
    assert "FOR UPDATE SKIP LOCKED" in source
    assert "lease_generation=%s" in source
    assert "lease_expires_at > clock_timestamp()" in source
    assert "execution_mode='paper'" in source
    assert "UPDATE order_intents SET status='paper_filled'" in source


def test_P5_3_Worker와_문서는_실제_주문_없이_paper만_처리한다() -> None:
    worker = WORKER.read_text()
    domain = DOMAIN.read_text()

    assert "PaperExecutionWorker" in worker
    assert "PaperExecutionFill" in worker
    assert "실제 Upbit 주문 제출 없이" in domain
    assert "paper_execution_jobs" in domain
