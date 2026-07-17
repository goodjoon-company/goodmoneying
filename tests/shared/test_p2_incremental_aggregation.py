from datetime import UTC, datetime, timedelta

import pytest

from goodmoneying_shared.incremental_aggregation import (
    MAX_OUTPUT_BUCKETS_PER_JOB,
    RollupInvalidationRange,
    affected_rollup_ranges,
    affected_rollup_ranges_for_interval,
    rollup_result_content_hash_values,
)


def test_과거_원천_한_건은_각_파생_단위의_포함_버킷만_무효화한다() -> None:
    changed_at = datetime(2026, 7, 17, 12, 37, tzinfo=UTC)

    ranges = affected_rollup_ranges([changed_at])

    assert {item.unit for item in ranges} == {
        "3m", "5m", "10m", "15m", "30m", "1h", "4h", "1d", "1w", "1M"
    }
    three_minute = next(item for item in ranges if item.unit == "3m")
    assert three_minute == RollupInvalidationRange(
        unit="3m",
        start_at=datetime(2026, 7, 17, 12, 36, tzinfo=UTC),
        end_at=datetime(2026, 7, 17, 12, 39, tzinfo=UTC),
        output_bucket_count=1,
    )


def test_연속_영향_버킷은_합치고_불연속_버킷은_분리한다() -> None:
    start = datetime(2026, 7, 17, tzinfo=UTC)

    ranges = [
        item
        for item in affected_rollup_ranges(
            [start, start + timedelta(minutes=3), start + timedelta(minutes=9)],
            units=("3m",),
        )
    ]

    assert ranges == [
        RollupInvalidationRange("3m", start, start + timedelta(minutes=6), 2),
        RollupInvalidationRange(
            "3m", start + timedelta(minutes=9), start + timedelta(minutes=12), 1
        ),
    ]


def test_연속_영향_범위는_최대_512개_출력_버킷으로_분할한다() -> None:
    start = datetime(2026, 7, 17, tzinfo=UTC)
    changed = [start + timedelta(minutes=3 * index) for index in range(513)]

    ranges = affected_rollup_ranges(changed, units=("3m",))

    assert [item.output_bucket_count for item in ranges] == [MAX_OUTPUT_BUCKETS_PER_JOB, 1]
    assert ranges[0].end_at == ranges[1].start_at


def test_영향_범위는_입력_순서와_중복에_무관하고_UTC가_아니면_거부한다() -> None:
    start = datetime(2026, 7, 17, tzinfo=UTC)
    expected = affected_rollup_ranges([start, start + timedelta(minutes=1)], units=("5m",))

    assert affected_rollup_ranges(
        [start + timedelta(minutes=1), start, start], units=("5m",)
    ) == expected
    with pytest.raises(ValueError, match="UTC"):
        affected_rollup_ranges([datetime(2026, 7, 17)], units=("5m",))


def test_커버리지_전이_범위도_512_버킷_상한으로_분할한다() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    ranges = affected_rollup_ranges_for_interval(
        start, start + timedelta(minutes=3 * 513), units=("3m",)
    )

    assert [item.output_bucket_count for item in ranges] == [512, 1]
    assert ranges[0].end_at == ranges[1].start_at


def test_결과_해시는_숫자_표현_차이와_음수_영을_같은_의미로_정규화한다() -> None:
    first = rollup_result_content_hash_values(
        "v1", "1.0", "2.00", "-0", "1.000", "3.0", "4.00", "complete", "available"
    )
    second = rollup_result_content_hash_values(
        "v1", "1", "2", "0.000", "1", "3", "4", "complete", "available"
    )

    assert first == second
