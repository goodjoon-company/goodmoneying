from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal, cast

DatasetKind = Literal["candle", "indicator", "market_statistic", "microstructure"]
DatasetQuality = Literal["available", "no_trade", "missing", "unavailable", "unverified"]
DatasetFillPolicy = Literal["none", "no_trade_carry_forward_v1"]
DatasetMissingPolicy = Literal["fail", "null", "drop"]
type CanonicalValue = (
    None
    | bool
    | int
    | str
    | Decimal
    | datetime
    | Sequence["CanonicalValue"]
    | Mapping[str, "CanonicalValue"]
)

_HASH_LENGTH = 64
_DATASET_KINDS = frozenset({"candle", "indicator", "market_statistic", "microstructure"})
_QUALITIES = frozenset({"available", "no_trade", "missing", "unavailable", "unverified"})
_FILL_POLICIES = frozenset({"none", "no_trade_carry_forward_v1"})
_MISSING_POLICIES = frozenset({"fail", "null", "drop"})


@dataclass(frozen=True)
class DatasetSeriesRequest:
    """요청 대리키와 이식 가능한 자연키를 함께 보존하는 시계열 선택."""

    instrument_id: int
    exchange: str
    market_code: str
    data_kind: DatasetKind
    unit: str | None
    definition_set_hash: str | None = None
    calculation_version: str | None = None


@dataclass(frozen=True)
class DatasetCanonicalSpecification:
    schema_version: str
    as_of: datetime
    input_start_at: datetime
    output_start_at: datetime
    end_at: datetime
    series: tuple[DatasetSeriesRequest, ...]
    fill_policy: DatasetFillPolicy
    missing_policy: DatasetMissingPolicy
    ordering_policy: str


@dataclass(frozen=True)
class DatasetCanonicalMember:
    """DB 대리키는 계보 참조일 뿐 정규 내용 해시에는 포함하지 않는다."""

    data_kind: DatasetKind
    exchange: str
    market_code: str
    unit: str | None
    occurred_at: datetime
    knowledge_at: datetime
    source_as_of: datetime
    content_hash: str
    quality: DatasetQuality
    calculation_version: str
    definition_hash: str | None
    source_ref_id: int


@dataclass(frozen=True)
class DatasetMarketStatusSnapshot:
    exchange: str
    market_code: str
    trading_status: str
    market_warning: str
    event_hash: str
    source_payload_checksum: str
    valid_from: datetime
    valid_to: datetime | None
    observed_at: datetime
    source_ref_id: int


@dataclass(frozen=True)
class DatasetCoverageSegment:
    data_kind: DatasetKind
    exchange: str
    market_code: str
    unit: str | None
    definition_set_hash: str | None
    calculation_version: str
    range_start_at: datetime
    range_end_at: datetime
    knowledge_at: datetime
    status: DatasetQuality
    observed_count: int
    expected_count: int
    evidence_hash: str
    source_ref_id: int | None


@dataclass(frozen=True)
class DatasetHashes:
    selection_hash: str
    manifest_hash: str
    market_status_hash: str
    coverage_hash: str
    content_hash: str


def canonical_json_bytes(payload: CanonicalValue) -> bytes:
    """참조 해시와 증분 해시가 공유하는 정규 JSON 바이트를 만든다."""

    return json.dumps(
        _canonical_value(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def canonical_payload_hash(payload: CanonicalValue) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


class CanonicalJsonArrayDigest:
    """정렬된 정규 객체 배열을 전체 적재하지 않고 SHA-256으로 축약한다."""

    def __init__(self) -> None:
        self._digest = hashlib.sha256(b"[")
        self._count = 0
        self._previous_key: tuple[object, ...] | None = None

    @property
    def count(self) -> int:
        return self._count

    def add(self, value: CanonicalValue, natural_key: tuple[object, ...]) -> bytes:
        if self._previous_key is not None and natural_key <= self._previous_key:
            raise ValueError("정규 JSON 배열 자연키는 오름차순이며 중복될 수 없다.")
        encoded = canonical_json_bytes(value)
        self.add_encoded(encoded, natural_key)
        return encoded

    def add_encoded(self, encoded: bytes, natural_key: tuple[object, ...]) -> None:
        if self._previous_key is not None and natural_key <= self._previous_key:
            raise ValueError("정규 JSON 배열 자연키는 오름차순이며 중복될 수 없다.")
        if self._count:
            self._digest.update(b",")
        self._digest.update(encoded)
        self._count += 1
        self._previous_key = natural_key

    def add_canonical_member(self, member: DatasetCanonicalMember) -> bytes:
        payload = _canonical_member(member)
        sort_key = _member_sort_key(payload)
        natural_key = sort_key[:-1]
        return self.add(payload, natural_key)

    def hexdigest(self) -> str:
        digest = self._digest.copy()
        digest.update(b"]")
        return digest.hexdigest()


def canonical_dataset_hashes(
    specification: DatasetCanonicalSpecification,
    members: Sequence[DatasetCanonicalMember],
    market_statuses: Sequence[DatasetMarketStatusSnapshot] = (),
    coverage: Sequence[DatasetCoverageSegment] = (),
) -> DatasetHashes:
    _validate_specification(specification)
    canonical_members = [_canonical_member(member) for member in members]
    canonical_members.sort(key=_member_sort_key)
    member_natural_keys = {
        (
            item["dataKind"],
            item["exchange"],
            item["marketCode"],
            item["unit"],
            item["definitionHash"],
            item["calculationVersion"],
            item["occurredAt"],
        )
        for item in canonical_members
    }
    if len(member_natural_keys) != len(canonical_members):
        raise ValueError("데이터셋 멤버 자연키와 내용이 중복되었다.")

    selection_hash = canonical_payload_hash(_canonical_specification(specification))
    manifest_hash = canonical_payload_hash(canonical_members)
    canonical_statuses = sorted(
        (_canonical_market_status(item) for item in market_statuses),
        key=lambda item: (
            str(item["exchange"]),
            str(item["marketCode"]),
            str(item["validFrom"]),
            str(item["observedAt"]),
        ),
    )
    _validate_status_projection(canonical_statuses)
    canonical_coverage = sorted(
        (_canonical_coverage_segment(item) for item in coverage),
        key=lambda item: (
            str(item["dataKind"]),
            str(item["exchange"]),
            str(item["marketCode"]),
            str(item["unit"]),
            str(item["definitionSetHash"]),
            str(item["calculationVersion"]),
            str(item["rangeStartAt"]),
        ),
    )
    _validate_coverage_projection(canonical_coverage)
    market_status_hash = canonical_payload_hash(canonical_statuses)
    coverage_hash = canonical_payload_hash(canonical_coverage)
    content_hash = canonical_payload_hash(
        {
            "selectionHash": selection_hash,
            "manifestHash": manifest_hash,
            "marketStatusHash": market_status_hash,
            "coverageHash": coverage_hash,
        }
    )
    return DatasetHashes(
        selection_hash=selection_hash,
        manifest_hash=manifest_hash,
        market_status_hash=market_status_hash,
        coverage_hash=coverage_hash,
        content_hash=content_hash,
    )


def validate_dataset_policies(
    *,
    series: Sequence[DatasetSeriesRequest],
    fill_policy: str,
    missing_policy: str,
) -> None:
    if fill_policy not in _FILL_POLICIES:
        raise ValueError(f"지원하지 않는 fill 정책이다: {fill_policy}")
    if missing_policy not in _MISSING_POLICIES:
        raise ValueError(f"지원하지 않는 missing 정책이다: {missing_policy}")
    if fill_policy == "no_trade_carry_forward_v1" and any(
        item.data_kind != "candle" for item in series
    ):
        raise ValueError("no_trade carry forward는 candle 시계열에만 허용된다.")


def _canonical_specification(
    specification: DatasetCanonicalSpecification,
) -> Mapping[str, CanonicalValue]:
    natural_series: list[dict[str, CanonicalValue]] = [
        {
            "exchange": item.exchange,
            "marketCode": item.market_code,
            "dataKind": item.data_kind,
            "unit": item.unit,
            "definitionSetHash": item.definition_set_hash,
            "calculationVersion": item.calculation_version,
        }
        for item in specification.series
    ]
    natural_series.sort(
        key=lambda item: (
            str(item["exchange"]),
            str(item["marketCode"]),
            str(item["dataKind"]),
            str(item["unit"]),
            str(item["definitionSetHash"]),
            str(item["calculationVersion"]),
        )
    )
    return {
        "schemaVersion": specification.schema_version,
        "asOf": specification.as_of,
        "inputStartAt": specification.input_start_at,
        "outputStartAt": specification.output_start_at,
        "endAt": specification.end_at,
        "series": natural_series,
        "fillPolicy": specification.fill_policy,
        "missingPolicy": specification.missing_policy,
        "orderingPolicy": specification.ordering_policy,
    }


def _canonical_member(member: DatasetCanonicalMember) -> dict[str, CanonicalValue]:
    _validate_utc(member.occurred_at, "occurred_at")
    _validate_utc(member.knowledge_at, "knowledge_at")
    _validate_utc(member.source_as_of, "source_as_of")
    _validate_hash(member.content_hash, "content_hash")
    if member.definition_hash is not None:
        _validate_hash(member.definition_hash, "definition_hash")
    if member.data_kind not in _DATASET_KINDS:
        raise ValueError(f"지원하지 않는 데이터 종류다: {member.data_kind}")
    if member.quality not in _QUALITIES:
        raise ValueError(f"지원하지 않는 데이터 품질이다: {member.quality}")
    return {
        "dataKind": member.data_kind,
        "exchange": member.exchange,
        "marketCode": member.market_code,
        "unit": member.unit,
        "occurredAt": member.occurred_at,
        "knowledgeAt": member.knowledge_at,
        "sourceAsOf": member.source_as_of,
        "contentHash": member.content_hash,
        "quality": member.quality,
        "calculationVersion": member.calculation_version,
        "definitionHash": member.definition_hash,
    }


def canonical_member_payload(member: DatasetCanonicalMember) -> dict[str, CanonicalValue]:
    return _canonical_member(member)


def _member_sort_key(item: Mapping[str, CanonicalValue]) -> tuple[str, ...]:
    return (
        str(item["dataKind"]),
        str(item["exchange"]),
        str(item["marketCode"]),
        str(item["unit"]),
        str(item["definitionHash"]),
        str(item["calculationVersion"]),
        str(item["occurredAt"]),
        str(item["contentHash"]),
    )


def _canonical_market_status(
    status: DatasetMarketStatusSnapshot,
) -> dict[str, CanonicalValue]:
    _validate_utc(status.valid_from, "valid_from")
    if status.valid_to is not None:
        _validate_utc(status.valid_to, "valid_to")
        if status.valid_to <= status.valid_from:
            raise ValueError("시장 상태 valid_to는 valid_from보다 뒤여야 한다.")
    _validate_utc(status.observed_at, "observed_at")
    _validate_hash(status.event_hash, "event_hash")
    return {
        "exchange": status.exchange,
        "marketCode": status.market_code,
        "tradingStatus": status.trading_status,
        "marketWarning": status.market_warning,
        "eventHash": status.event_hash,
        "sourcePayloadChecksum": status.source_payload_checksum,
        "validFrom": status.valid_from,
        "validTo": status.valid_to,
        "observedAt": status.observed_at,
    }


def canonical_market_status_payload(
    status: DatasetMarketStatusSnapshot,
) -> dict[str, CanonicalValue]:
    return _canonical_market_status(status)


def _canonical_coverage_segment(
    segment: DatasetCoverageSegment,
) -> dict[str, CanonicalValue]:
    _validate_utc(segment.range_start_at, "range_start_at")
    _validate_utc(segment.range_end_at, "range_end_at")
    _validate_utc(segment.knowledge_at, "knowledge_at")
    if segment.definition_set_hash is not None:
        _validate_hash(segment.definition_set_hash, "definition_set_hash")
    if segment.range_start_at >= segment.range_end_at:
        raise ValueError("coverage 범위가 비어 있다.")
    if segment.status not in _QUALITIES:
        raise ValueError(f"지원하지 않는 coverage 상태다: {segment.status}")
    if segment.observed_count < 0 or segment.expected_count < 0:
        raise ValueError("coverage count는 음수일 수 없다.")
    _validate_hash(segment.evidence_hash, "evidence_hash")
    return {
        "dataKind": segment.data_kind,
        "exchange": segment.exchange,
        "marketCode": segment.market_code,
        "unit": segment.unit,
        "definitionSetHash": segment.definition_set_hash,
        "calculationVersion": segment.calculation_version,
        "rangeStartAt": segment.range_start_at,
        "rangeEndAt": segment.range_end_at,
        "knowledgeAt": segment.knowledge_at,
        "status": segment.status,
        "observedCount": segment.observed_count,
        "expectedCount": segment.expected_count,
        "evidenceHash": segment.evidence_hash,
    }


def canonical_coverage_payload(
    segment: DatasetCoverageSegment,
) -> dict[str, CanonicalValue]:
    return _canonical_coverage_segment(segment)


def _validate_specification(specification: DatasetCanonicalSpecification) -> None:
    for name in ("as_of", "input_start_at", "output_start_at", "end_at"):
        _validate_utc(getattr(specification, name), name)
    if not (
        specification.input_start_at
        <= specification.output_start_at
        < specification.end_at
        <= specification.as_of
    ):
        raise ValueError("데이터셋 시간 범위는 input <= output < end <= asOf여야 한다.")
    if not specification.series:
        raise ValueError("데이터셋에는 하나 이상의 시계열이 필요하다.")
    natural_keys: set[tuple[object, ...]] = set()
    for item in specification.series:
        if item.instrument_id <= 0:
            raise ValueError("instrument_id는 양수여야 한다.")
        if item.data_kind not in _DATASET_KINDS:
            raise ValueError(f"지원하지 않는 데이터 종류다: {item.data_kind}")
        if not item.unit:
            raise ValueError("시계열 unit이 필요하다.")
        identity = (
            item.exchange,
            item.market_code,
            item.data_kind,
            item.unit,
            item.definition_set_hash,
            item.calculation_version,
        )
        if identity in natural_keys:
            raise ValueError("데이터셋 series 자연키가 중복되었다.")
        natural_keys.add(identity)
    validate_dataset_policies(
        series=specification.series,
        fill_policy=specification.fill_policy,
        missing_policy=specification.missing_policy,
    )


def _validate_utc(value: datetime, name: str) -> None:
    offset = value.utcoffset()
    if value.tzinfo is None or offset is None or offset.total_seconds() != 0:
        raise ValueError(f"{name}은 UTC timezone-aware datetime이어야 한다.")


def _validate_hash(value: str, name: str) -> None:
    if len(value) != _HASH_LENGTH or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError(f"{name}는 소문자 SHA-256이어야 한다.")


def _validate_status_projection(statuses: Sequence[Mapping[str, CanonicalValue]]) -> None:
    seen: set[tuple[object, ...]] = set()
    previous: dict[tuple[object, object], datetime] = {}
    for item in statuses:
        identity = (
            item["exchange"],
            item["marketCode"],
            item["validFrom"],
            item["observedAt"],
        )
        if identity in seen:
            raise ValueError("시장 상태 snapshot 자연키가 중복되었다.")
        seen.add(identity)
        market = (item["exchange"], item["marketCode"])
        valid_from = cast(datetime, item["validFrom"])
        previous_end = previous.get(market)
        if previous_end is not None and valid_from < previous_end:
            raise ValueError("시장 상태 snapshot 구간이 겹친다.")
        valid_to = cast(datetime | None, item["validTo"])
        previous[market] = valid_to or datetime.max.replace(tzinfo=UTC)


def _validate_coverage_projection(
    coverage: Sequence[Mapping[str, CanonicalValue]],
) -> None:
    seen: set[tuple[object, ...]] = set()
    previous: dict[tuple[object, ...], datetime] = {}
    for item in coverage:
        series = (
            item["dataKind"],
            item["exchange"],
            item["marketCode"],
            item["unit"],
            item["definitionSetHash"],
            item["calculationVersion"],
        )
        identity = (*series, item["rangeStartAt"], item["rangeEndAt"])
        if identity in seen:
            raise ValueError("coverage snapshot 자연키가 중복되었다.")
        seen.add(identity)
        range_start = cast(datetime, item["rangeStartAt"])
        previous_end = previous.get(series)
        if previous_end is not None and range_start < previous_end:
            raise ValueError("coverage snapshot 구간이 겹친다.")
        previous[series] = cast(datetime, item["rangeEndAt"])


def _canonical_value(value: CanonicalValue) -> object:
    if isinstance(value, datetime):
        _validate_utc(value, "datetime")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, Mapping):
        return {key: _canonical_value(item) for key, item in sorted(value.items())}
    if isinstance(value, Sequence) and not isinstance(value, str):
        return [_canonical_value(item) for item in value]
    return value
