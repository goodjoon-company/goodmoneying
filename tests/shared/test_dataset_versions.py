from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from goodmoneying_shared import dataset_versions
from goodmoneying_shared.dataset_versions import (
    DatasetCanonicalMember,
    DatasetCanonicalSpecification,
    DatasetCoverageSegment,
    DatasetMarketStatusSnapshot,
    DatasetSeriesRequest,
    canonical_dataset_hashes,
    canonical_payload_hash,
    validate_dataset_policies,
)

AS_OF = datetime(2026, 7, 17, 6, tzinfo=UTC)
START = datetime(2026, 7, 17, 0, tzinfo=UTC)
END = datetime(2026, 7, 17, 2, tzinfo=UTC)
HASH_A = "a" * 64
HASH_B = "b" * 64


def _specification() -> DatasetCanonicalSpecification:
    return DatasetCanonicalSpecification(
        schema_version="dataset-v1",
        as_of=AS_OF,
        input_start_at=START,
        output_start_at=START,
        end_at=END,
        series=(
            DatasetSeriesRequest(
                instrument_id=41,
                exchange="UPBIT",
                market_code="KRW-BTC",
                data_kind="candle",
                unit="1m",
            ),
        ),
        fill_policy="none",
        missing_policy="fail",
        ordering_policy="market-kind-unit-time-v1",
    )


def _member(
    *, source_ref_id: int = 1, occurred_at: datetime = START
) -> DatasetCanonicalMember:
    return DatasetCanonicalMember(
        data_kind="candle",
        exchange="UPBIT",
        market_code="KRW-BTC",
        unit="1m",
        occurred_at=occurred_at,
        knowledge_at=AS_OF,
        source_as_of=occurred_at,
        content_hash=HASH_A,
        quality="available",
        calculation_version="source-candle-v1",
        definition_hash=None,
        source_ref_id=source_ref_id,
    )


def test_데이터셋_해시는_멤버_순서와_DB_대리키에_독립적이다() -> None:
    members = (
        _member(source_ref_id=11, occurred_at=START),
        replace(_member(source_ref_id=12), occurred_at=datetime(2026, 7, 17, 1, tzinfo=UTC)),
    )

    first = canonical_dataset_hashes(_specification(), members)
    second = canonical_dataset_hashes(
        replace(_specification(), series=(replace(_specification().series[0], instrument_id=999),)),
        tuple(
            replace(member, source_ref_id=900 + index)
            for index, member in enumerate(reversed(members))
        ),
    )

    assert first == second
    assert len(first.selection_hash) == 64
    assert len(first.manifest_hash) == 64
    assert len(first.content_hash) == 64


def test_데이터셋_해시는_자연키_내용_정책이_바뀌면_달라진다() -> None:
    baseline = canonical_dataset_hashes(_specification(), (_member(),))

    assert (
        canonical_dataset_hashes(
            replace(_specification(), missing_policy="null"), (_member(),)
        ).content_hash
        != baseline.content_hash
    )
    assert (
        canonical_dataset_hashes(
            _specification(), (replace(_member(), content_hash=HASH_B),)
        ).content_hash
        != baseline.content_hash
    )
    assert (
        canonical_dataset_hashes(
            replace(
                _specification(),
                series=(replace(_specification().series[0], market_code="KRW-ETH"),),
            ),
            (replace(_member(), market_code="KRW-ETH"),),
        ).content_hash
        != baseline.content_hash
    )


def test_같은_멤버_자연키의_다른_content도_중복으로_거부한다() -> None:
    with pytest.raises(ValueError, match="멤버 자연키"):
        canonical_dataset_hashes(
            _specification(),
            (_member(), replace(_member(), content_hash=HASH_B, source_ref_id=2)),
        )


def test_coverage_projection은_같은_series의_겹치는_구간을_거부한다() -> None:
    first = DatasetCoverageSegment(
        data_kind="candle",
        exchange="UPBIT",
        market_code="KRW-BTC",
        unit="1m",
        definition_set_hash=None,
        calculation_version="source-candle-v1",
        range_start_at=START,
        range_end_at=END,
        knowledge_at=AS_OF,
        status="available",
        observed_count=120,
        expected_count=120,
        evidence_hash=HASH_A,
        source_ref_id=1,
    )
    overlap = replace(
        first,
        range_start_at=datetime(2026, 7, 17, 1, tzinfo=UTC),
        range_end_at=AS_OF,
        evidence_hash=HASH_B,
        source_ref_id=2,
    )

    with pytest.raises(ValueError, match="coverage snapshot 구간"):
        canonical_dataset_hashes(_specification(), (_member(),), coverage=(first, overlap))


def test_정규_payload_해시는_키_순서와_소수_표현을_정규화한다() -> None:
    assert canonical_payload_hash({"price": "1.0", "nested": {"b": 2, "a": True}}) == (
        canonical_payload_hash({"nested": {"a": True, "b": 2}, "price": "1.0"})
    )


@pytest.mark.parametrize("missing_policy", ["fail", "null", "drop"])
def test_v1_missing_정책은_세_의미를_분리한다(missing_policy: str) -> None:
    validate_dataset_policies(
        series=_specification().series,
        fill_policy="none",
        missing_policy=missing_policy,
    )


def test_no_trade_carry_forward는_candle에만_허용한다() -> None:
    validate_dataset_policies(
        series=_specification().series,
        fill_policy="no_trade_carry_forward_v1",
        missing_policy="fail",
    )

    with pytest.raises(ValueError, match="candle"):
        validate_dataset_policies(
            series=(replace(_specification().series[0], data_kind="microstructure"),),
            fill_policy="no_trade_carry_forward_v1",
            missing_policy="fail",
        )


def test_시간은_UTC_timezone_aware여야_한다() -> None:
    with pytest.raises(ValueError, match="UTC"):
        canonical_dataset_hashes(
            replace(_specification(), as_of=datetime(2026, 7, 17, 6)),
            (_member(),),
        )


def test_시장상태와_coverage는_대리키와_순서에_독립적으로_내용해시에_포함된다() -> None:
    statuses = (
        DatasetMarketStatusSnapshot(
            exchange="UPBIT",
            market_code="KRW-BTC",
            trading_status="active",
            market_warning="NONE",
            event_hash=HASH_A,
            source_payload_checksum="source-a",
            valid_from=START,
            valid_to=END,
            observed_at=AS_OF,
            source_ref_id=10,
        ),
    )
    coverage = (
        DatasetCoverageSegment(
            data_kind="candle",
            exchange="UPBIT",
            market_code="KRW-BTC",
            unit="1m",
            definition_set_hash=None,
            calculation_version="source-candle-v1",
            range_start_at=START,
            range_end_at=END,
            knowledge_at=AS_OF,
            status="available",
            observed_count=120,
            expected_count=120,
            evidence_hash=HASH_B,
            source_ref_id=20,
        ),
    )

    baseline = canonical_dataset_hashes(_specification(), (_member(),), statuses, coverage)
    same = canonical_dataset_hashes(
        _specification(),
        (_member(source_ref_id=999),),
        tuple(replace(item, source_ref_id=998) for item in reversed(statuses)),
        tuple(replace(item, source_ref_id=997) for item in reversed(coverage)),
    )

    assert baseline == same
    assert (
        canonical_dataset_hashes(
            _specification(),
            (_member(),),
            (replace(statuses[0], trading_status="delisted"),),
            coverage,
        ).content_hash
        != baseline.content_hash
    )
    assert (
        canonical_dataset_hashes(
            _specification(),
            (_member(),),
            statuses,
            (replace(coverage[0], observed_count=119),),
        ).content_hash
        != baseline.content_hash
    )
    assert (
        canonical_dataset_hashes(
            _specification(),
            (_member(),),
            statuses,
            (replace(coverage[0], knowledge_at=AS_OF - timedelta(seconds=1)),),
        ).content_hash
        != baseline.content_hash
    )


def test_정의와_계산버전이_다른_동일기본_series는_별도_자연키다() -> None:
    specification = replace(
        _specification(),
        series=(
            DatasetSeriesRequest(
                instrument_id=41,
                exchange="UPBIT",
                market_code="KRW-BTC",
                data_kind="indicator",
                unit="1m",
                definition_set_hash=HASH_A,
                calculation_version="indicator-v1",
            ),
            DatasetSeriesRequest(
                instrument_id=41,
                exchange="UPBIT",
                market_code="KRW-BTC",
                data_kind="indicator",
                unit="1m",
                definition_set_hash=HASH_B,
                calculation_version="indicator-v1",
            ),
        ),
    )
    members = (
        replace(
            _member(),
            data_kind="indicator",
            calculation_version="indicator-v1",
            definition_hash=HASH_A,
        ),
        replace(
            _member(source_ref_id=2),
            data_kind="indicator",
            calculation_version="indicator-v1",
            definition_hash=HASH_B,
            content_hash=HASH_B,
        ),
    )

    hashes = canonical_dataset_hashes(specification, members)

    assert len(hashes.content_hash) == 64


@pytest.mark.parametrize(
    "payloads",
    (
        (),
        ({"key": "one", "at": START},),
        (
            {"key": "one", "at": START},
            {"key": "two", "at": START + timedelta(minutes=1)},
            {"key": "three", "at": START + timedelta(minutes=2)},
        ),
    ),
)
def test_증분_JSON_배열_해시는_참조_구현과_byte_단위로_같다(
    payloads: tuple[dataset_versions.CanonicalValue, ...],
) -> None:
    digest_type = getattr(dataset_versions, "CanonicalJsonArrayDigest", None)
    assert digest_type is not None
    digest = digest_type()
    for index, payload in enumerate(payloads):
        digest.add(payload, (index,))

    assert digest.hexdigest() == canonical_payload_hash(payloads)


def test_증분_JSON_배열은_chunk_경계와_대리키에_독립적이다() -> None:
    digest_type = getattr(dataset_versions, "CanonicalJsonArrayDigest", None)
    assert digest_type is not None
    members = tuple(
        _member(source_ref_id=index + 1, occurred_at=START + timedelta(minutes=index))
        for index in range(9)
    )
    expected = canonical_dataset_hashes(_specification(), members).manifest_hash

    digest = digest_type()
    for chunk_start in range(0, len(members), 4):
        for member in members[chunk_start : chunk_start + 4]:
            digest.add_canonical_member(
                replace(member, source_ref_id=member.source_ref_id + 10_000)
            )

    assert digest.hexdigest() == expected


def test_증분_JSON_배열은_인접한_중복_자연키를_거부한다() -> None:
    digest_type = getattr(dataset_versions, "CanonicalJsonArrayDigest", None)
    assert digest_type is not None
    digest = digest_type()
    digest.add({"value": 1}, ("same",))

    with pytest.raises(ValueError, match="자연키"):
        digest.add({"value": 2}, ("same",))


def test_selection_hash는_동일_기본_series의_입력_순서에_독립적이다() -> None:
    series = (
        DatasetSeriesRequest(
            instrument_id=41,
            exchange="UPBIT",
            market_code="KRW-BTC",
            data_kind="indicator",
            unit="1m",
            definition_set_hash=HASH_A,
            calculation_version="indicator-v1",
        ),
        DatasetSeriesRequest(
            instrument_id=41,
            exchange="UPBIT",
            market_code="KRW-BTC",
            data_kind="indicator",
            unit="1m",
            definition_set_hash=HASH_B,
            calculation_version="indicator-v2",
        ),
    )

    first = canonical_dataset_hashes(replace(_specification(), series=series), ())
    second = canonical_dataset_hashes(
        replace(_specification(), series=tuple(reversed(series))), ()
    )

    assert first.selection_hash == second.selection_hash
