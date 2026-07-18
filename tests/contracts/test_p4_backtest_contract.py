from __future__ import annotations

from pathlib import Path

CONTRACT = Path("docs/contracts/backtest-engine.md")
DOMAIN = Path("docs/02_Architecture/system-trading-domain.md")


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
