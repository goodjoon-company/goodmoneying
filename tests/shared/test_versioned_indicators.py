from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, getcontext

from goodmoneying_shared.models import CandleView
from goodmoneying_shared.versioned_indicators import (
    INDICATOR_DEFINITION_VERSIONS,
    calculate_indicator_series,
)


def _candles(values: list[str], *, offset: int = 0) -> list[CandleView]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        CandleView(
            started_at=start + timedelta(minutes=offset + index),
            open=Decimal(value),
            high=Decimal(value),
            low=Decimal(value),
            close=Decimal(value),
            volume=Decimal("1"),
            trade_amount=Decimal(value),
            completeness="complete",
            source_as_of=start + timedelta(minutes=offset + index, seconds=10),
            knowledge_at=start + timedelta(minutes=offset + index, seconds=20),
            input_revision_ids=(offset + index + 1,),
            rollup_id=offset + index + 100,
            source_revision_through_id=offset + index + 1,
            quality_event_through_id=offset + index + 10,
        )
        for index, value in enumerate(values)
    ]


def test_지표_정의_버전은_알고리즘과_십진수_정책을_불변_해시로_식별한다() -> None:
    assert set(INDICATOR_DEFINITION_VERSIONS) == {
        "sma20",
        "sma60",
        "ema20",
        "bollinger20",
        "rsi14",
    }
    assert all(len(item.definition_hash) == 64 for item in INDICATOR_DEFINITION_VERSIONS.values())
    assert all(item.decimal_precision == 50 for item in INDICATOR_DEFINITION_VERSIONS.values())
    assert all(
        item.rounding == "ROUND_HALF_EVEN" for item in INDICATOR_DEFINITION_VERSIONS.values()
    )


def test_고정_벡터는_SMA_EMA_볼린저_RSI를_정확한_Decimal로_계산한다() -> None:
    points = calculate_indicator_series(_candles([str(value) for value in range(1, 61)]))

    nineteenth = points[18]
    twentieth = points[19]
    sixtieth = points[59]
    assert nineteenth.statuses == {
        "sma20": "warming_up",
        "sma60": "warming_up",
        "ema20": "warming_up",
        "bollinger20": "warming_up",
        "rsi14": "ready",
    }
    assert twentieth.values["sma20"] == Decimal("10.5")
    assert twentieth.values["ema20"] == Decimal("10.5")
    assert twentieth.values["bollingerMiddle"] == Decimal("10.5")
    assert twentieth.values["bollingerUpper"] == Decimal(
        "22.032562594670795889354183238817872500583068851241"
    )
    assert twentieth.values["bollingerLower"] == Decimal(
        "-1.032562594670795889354183238817872500583068851241"
    )
    assert twentieth.values["rsi14"] == Decimal("100")
    assert sixtieth.values["sma60"] == Decimal("30.5")
    assert getcontext().prec >= 28  # 모듈이 전역 Decimal 문맥을 오염시키지 않는다.
    assert getcontext().rounding == ROUND_HALF_EVEN


def test_EMA는_첫_20개_SMA를_seed로_쓰고_RSI_Wilder_경계값을_정의한다() -> None:
    increasing = calculate_indicator_series(_candles([str(value) for value in range(1, 22)]))
    assert increasing[19].values["ema20"] == Decimal("10.5")
    assert increasing[20].values["ema20"] == Decimal("11.5")

    flat = calculate_indicator_series(_candles(["7"] * 20))
    falling = calculate_indicator_series(_candles([str(value) for value in range(20, 0, -1)]))
    assert flat[14].values["rsi14"] == Decimal("50")
    assert falling[14].values["rsi14"] == Decimal("0")


def test_조회_범위_앞_warmup을_포함하면_부분_조회와_전체_조회_결과가_같다() -> None:
    candles = _candles([str(value) for value in range(1, 101)])
    full = calculate_indicator_series(candles)
    requested_from = candles[80].started_at
    ranged = calculate_indicator_series(candles, requested_from=requested_from)

    assert ranged == tuple(point for point in full if point.started_at >= requested_from)


def test_품질_결측은_0을_합성하지_않고_모든_지표_상태와_계산_연속성을_초기화한다() -> None:
    candles = _candles(["1"] * 70)
    candles[40] = CandleView(
        **{**candles[40].__dict__, "quality": "missing", "completeness": "partial"}
    )

    points = calculate_indicator_series(candles)

    assert set(points[40].values.values()) == {None}
    assert set(points[40].statuses.values()) == {"missing"}
    assert points[41].statuses["ema20"] == "warming_up"
    assert points[59].statuses["ema20"] == "warming_up"
    assert points[60].statuses["ema20"] == "ready"


def test_no_trade_unavailable_unverified_partial은_모두_계산을_초기화한다() -> None:
    for quality, completeness in (
        ("no_trade", "empty"),
        ("unavailable", "partial"),
        ("unverified", "partial"),
        ("available", "partial"),
    ):
        candles = _candles(["1"] * 22)
        candles[20] = CandleView(
            **{
                **candles[20].__dict__,
                "quality": quality,
                "completeness": completeness,
            }
        )
        points = calculate_indicator_series(candles)
        assert set(points[20].statuses.values()) == {"missing"}
        assert set(points[21].statuses.values()) == {"warming_up"}


def test_지표_포인트는_현재_입력과_원천_품질_frontier_체크포인트를_보존한다() -> None:
    points = calculate_indicator_series(_candles([str(value) for value in range(1, 21)]))
    point = points[-1]

    assert point.rollup_ids == (119,)
    assert point.source_revision_through_id == 20
    assert point.quality_event_through_id == 29
    assert point.source_as_of == datetime(2026, 1, 1, 0, 19, 10, tzinfo=UTC)
    assert point.knowledge_at == datetime(2026, 1, 1, 0, 19, 20, tzinfo=UTC)
    assert point.checkpoint_state["consecutiveCount"] == 20
    recent_closes = point.checkpoint_state["recentCloses"]
    assert isinstance(recent_closes, list)
    assert len(recent_closes) == 20


def test_EMA와_Wilder_RSI는_O_n2_배열_대신_고정크기_checkpoint를_보존한다() -> None:
    point = calculate_indicator_series(_candles([str(value) for value in range(1, 101)]))[-1]

    assert point.lineage_by_indicator == {}
    assert point.rollup_ids == (199,)
    assert point.checkpoint_state["consecutiveCount"] == 100
    recent_closes = point.checkpoint_state["recentCloses"]
    assert isinstance(recent_closes, list)
    assert len(recent_closes) == 60


def test_checkpoint에서_이어_계산한_최신_append는_전체_재계산과_같다() -> None:
    candles = _candles([str(value) for value in range(1, 102)])
    first = calculate_indicator_series(candles[:100])
    appended = calculate_indicator_series(
        candles[100:], initial_checkpoint=first[-1].checkpoint_state
    )
    full = calculate_indicator_series(candles)

    assert appended == full[100:]


def test_완전히_빈_품질_구간으로_행이_건너뛰면_시간_gap에서_계산을_reset한다() -> None:
    candles = _candles(["1"] * 40)
    without_missing_row = candles[:20] + candles[21:]

    points = calculate_indicator_series(without_missing_row, unit="1m")

    assert points[20].started_at == candles[21].started_at
    assert set(points[20].statuses.values()) == {"warming_up"}


def test_같은_startedAt의_복수_rollup_개정은_projection_미고정으로_거부한다() -> None:
    candles = _candles(["1"] * 20)
    duplicate_revision = CandleView(
        **{**candles[-1].__dict__, "rollup_id": 999, "close": Decimal("2")}
    )

    try:
        calculate_indicator_series([*candles, duplicate_revision], unit="1m")
    except ValueError as exc:
        assert "projection" in str(exc)
    else:
        raise AssertionError("동일 시각 복수 개정은 계산 전에 거부해야 한다.")
