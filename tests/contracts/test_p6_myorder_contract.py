from __future__ import annotations

from pathlib import Path

CONTRACT = Path("docs/contracts/upbit/myorder-event.md")
DOMAIN = Path("docs/02_Architecture/system-trading-domain.md")
TASK = Path("docs/Task/P6.md")
PARSER = Path("packages/shared/goodmoneying_shared/upbit_myorder.py")


def test_P6_4_myOrder_계약은_무이벤트와_재주문_금지를_명시한다() -> None:
    contract = CONTRACT.read_text()
    domain = DOMAIN.read_text()
    task = TASK.read_text()

    assert "initial snapshot을 보내지 않는다" in contract
    assert "무이벤트는 정상" in contract
    assert "REST snapshot 대사를 수행한다" in contract
    assert "재주문하지 않는다" in contract
    assert "`prevented_volume`" in contract
    assert "`prevented_locked`" in contract
    assert "`trade_fee`" in contract
    assert "`is_maker`" in contract
    assert "initial snapshot 없이 실제 주문·체결 event만 보낸다" in domain
    assert "P6-4 private myOrder 대사 입력 계약" in task


def test_P6_4_myOrder_parser는_SMP와_부분체결_필드를_보존한다() -> None:
    source = PARSER.read_text()

    assert "class UpbitMyOrderEvent" in source
    assert "prevented_volume" in source
    assert "prevented_locked" in source
    assert "trade_fee" in source
    assert "is_maker" in source
    assert "can_resubmit=False" in source
