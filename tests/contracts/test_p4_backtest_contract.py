from __future__ import annotations

from pathlib import Path

CONTRACT = Path("docs/contracts/backtest-engine.md")
DOMAIN = Path("docs/02_Architecture/system-trading-domain.md")
PROGRESS_WS = Path("docs/contracts/api/backtest-progress-websocket.md")


def test_P4_1_백테스트_계약은_결정론과_입력_hash를_명시한다() -> None:
    text = CONTRACT.read_text()

    assert "backtest-core-v1" in text
    assert "dataset content hash" in text
    assert "strategy graph hash" in text
    assert "parameter hash" in text
    assert "deterministic seed" in text
    assert "wall clock" in text
    assert "Decimal" in text


def test_P4_1_백테스트_계약은_체결_가정과_부분체결을_기록한다() -> None:
    text = CONTRACT.read_text()

    assert "orderbook_absent_uses_candle_close" in text
    assert "partial_fill_by_candle_volume_participation" in text
    assert "fee_rate" in text
    assert "slippage_bps" in text
    assert "latency_seconds" in text


def test_P4_1_도메인_아키텍처는_golden_replay_동등성을_요구한다() -> None:
    text = DOMAIN.read_text()

    assert "golden replay" in text
    assert "공통 전략 평가기" in text
    assert "신호 동등성" in text


def test_P4_8_백테스트_계약은_성과_artifact_스키마를_명시한다() -> None:
    text = CONTRACT.read_text()
    domain = DOMAIN.read_text()

    assert "walk_forward_summary" in text
    assert "sensitivity_summary" in text
    assert "bootstrap_summary" in text
    assert "backtest-artifact-walk-forward-v1" in text
    assert "backtest-artifact-sensitivity-v1" in text
    assert "backtest-artifact-bootstrap-v1" in text
    assert "contentHash" in text
    assert "P4-8" in domain


def test_P4_9_백테스트_진행_WebSocket_계약을_명시한다() -> None:
    text = PROGRESS_WS.read_text()
    contract = CONTRACT.read_text()

    assert "/v1/backtest-runs/{backtestRunId}/progress" in text
    assert "backtest.progress" in text
    assert "backtest.error" in text
    assert "BACKTEST_RUN_NOT_FOUND" in text
    assert "pending" in text
    assert "running" in text
    assert "succeeded" in text
    assert "P4-9" in contract
