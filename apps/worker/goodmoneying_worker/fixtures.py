from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from goodmoneying_shared.time import minute_bucket, now_kst

CORE_MARKETS = [
    ("KRW-BTC", "비트코인"),
    ("KRW-ETH", "이더리움"),
    ("KRW-XRP", "리플"),
    ("KRW-SOL", "솔라나"),
    ("KRW-DOGE", "도지코인"),
]


def fixture_market_codes(size: int = 100) -> list[tuple[str, str]]:
    markets = list(CORE_MARKETS)
    for index in range(len(markets) + 1, size + 1):
        markets.append((f"KRW-GM{index:03d}", f"굿머니코인 {index:03d}"))
    return markets[:size]


def fixture_ticker_rows(size: int = 100) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for rank, (market_code, display_name) in enumerate(fixture_market_codes(size), start=1):
        base_price = Decimal("100000000") / Decimal(rank)
        acc_trade_price_24h = Decimal("100000000000") / Decimal(rank)
        rows.append(
            {
                "market": market_code,
                "display_name": display_name,
                "trade_price": str(base_price.quantize(Decimal("0.0001"))),
                "acc_trade_price_24h": str(acc_trade_price_24h.quantize(Decimal("0.0001"))),
                "signed_change_rate": str(
                    (Decimal(rank % 9) / Decimal("1000")).quantize(Decimal("0.0001"))
                ),
            }
        )
    return rows


def fixture_orderbook_rows(markets: list[str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for index, market_code in enumerate(markets, start=1):
        best_bid = Decimal("100000") + Decimal(index)
        best_ask = best_bid + Decimal("10")
        bid_depth = Decimal("1000") + Decimal(index)
        ask_depth = Decimal("900") + Decimal(index)
        imbalance = (bid_depth - ask_depth) / (bid_depth + ask_depth)
        rows.append(
            {
                "market": market_code,
                "best_bid_price": str(best_bid),
                "best_bid_size": "1.5",
                "best_ask_price": str(best_ask),
                "best_ask_size": "1.2",
                "spread": str(best_ask - best_bid),
                "bid_depth_10": str(bid_depth),
                "ask_depth_10": str(ask_depth),
                "imbalance_10": str(imbalance.quantize(Decimal("0.0001"))),
            }
        )
    return rows


def fixture_candle_rows(markets: list[str], minutes: int = 5) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    base_start = minute_bucket(now_kst()) - timedelta(minutes=minutes)
    for market_index, market_code in enumerate(markets, start=1):
        for offset in range(minutes):
            start_at = base_start + timedelta(minutes=offset)
            open_price = Decimal("100000") + Decimal(market_index * 10 + offset)
            close_price = open_price + Decimal("3")
            rows.append(
                {
                    "market": market_code,
                    "candle_unit": "1m",
                    "candle_start_at": start_at.isoformat(),
                    "open_price": str(open_price),
                    "high_price": str(close_price + Decimal("1")),
                    "low_price": str(open_price - Decimal("1")),
                    "close_price": str(close_price),
                    "trade_volume": "12.5",
                    "trade_amount": str(
                        (close_price * Decimal("12.5")).quantize(Decimal("0.0001"))
                    ),
                }
            )
    return rows
