# ruff: noqa: E501
# mypy: disable-error-code="no-any-return"
from __future__ import annotations

import base64
import json
from collections.abc import Mapping, Sequence
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from threading import Event, Lock, Thread
from typing import Any, cast

from psycopg import errors
from psycopg.types.json import Jsonb

from goodmoneying_shared.dataset_versions import (
    CanonicalJsonArrayDigest,
    DatasetCanonicalMember,
    DatasetCanonicalSpecification,
    DatasetCoverageSegment,
    DatasetHashes,
    DatasetMarketStatusSnapshot,
    DatasetSeriesRequest,
    canonical_coverage_payload,
    canonical_dataset_hashes,
    canonical_market_status_payload,
    canonical_payload_hash,
    validate_dataset_policies,
)

Row = dict[str, Any]
_QUALITY_ORDER = {
    "available": 0,
    "no_trade": 1,
    "missing": 2,
    "unavailable": 3,
    "unverified": 4,
}


class DatasetIdempotencyConflictError(ValueError):
    """같은 멱등 키가 다른 정규 요청 내용을 가리킨다."""


class DatasetBuildPublicationError(RuntimeError):
    """고정된 정책으로 데이터셋을 발행할 수 없다."""


class DatasetBuildLeaseLostError(RuntimeError):
    """발행 worker가 임대 소유권 또는 generation을 잃었다."""


class DatasetCursorMismatchError(ValueError):
    """cursor가 발급된 데이터셋·시계열·범위와 현재 요청이 다르다."""


class PostgresDatasetVersionStore:
    def __init__(self, repository: object) -> None:
        self._repository = repository

    def create_build(self, **arguments: object) -> Row:
        return create_build(self._repository, **arguments)

    def get_build(self, build_id: int) -> Row | None:
        return get_build(self._repository, build_id)

    def get_version(self, dataset_version_id: int) -> Row | None:
        return get_version(self._repository, dataset_version_id)

    def list_versions(self, **arguments: object) -> Row:
        return list_versions(self._repository, **arguments)

    def get_coverage(self, dataset_version_id: int) -> Row | None:
        return get_coverage(self._repository, dataset_version_id)

    def get_series(self, **arguments: object) -> Row | None:
        return get_series(self._repository, **arguments)

    def publish_next_build(self, worker_id: str) -> int:
        return publish_next_build(self._repository, worker_id)


def create_build(
    repository: object,
    *,
    request_id: object,
    idempotency_key: object,
    actor_id: object,
    requested_at: object,
    reason: object,
    selection: object,
    policies: object,
) -> Row:
    connector = _connector(repository)
    parsed = _parse_request(
        request_id=request_id,
        idempotency_key=idempotency_key,
        actor_id=actor_id,
        requested_at=requested_at,
        reason=reason,
        selection=selection,
        policies=policies,
    )
    for attempt in range(3):
        try:
            with connector() as connection:
                connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"dataset-build:{parsed['idempotency_key']}",),
                )
                existing = connection.execute(
                    "SELECT * FROM dataset_builds WHERE idempotency_key=%s",
                    (parsed["idempotency_key"],),
                ).fetchone()
                if existing is not None:
                    if existing["request_hash"] != parsed["request_hash"]:
                        raise DatasetIdempotencyConflictError(
                            "멱등 키의 기존 데이터셋 빌드와 요청 내용이 다르다."
                        )
                    return _build_response(existing)
                build = connection.execute(
                    """
                    INSERT INTO dataset_builds (
                      idempotency_key, request_id, actor_id, requested_at, reason,
                      request_hash, schema_version, as_of, input_start_at,
                      output_start_at, end_at, fill_policy, missing_policy,
                      ordering_policy, request_payload
                    ) VALUES (
                      %s,%s,%s,%s,%s,%s,'dataset-v1',%s,%s,%s,%s,%s,%s,
                      'market-kind-unit-time-v1',%s
                    ) RETURNING *
                    """,
                    (
                        parsed["idempotency_key"],
                        parsed["request_id"],
                        parsed["actor_id"],
                        parsed["requested_at"],
                        parsed["reason"],
                        parsed["request_hash"],
                        parsed["as_of"],
                        parsed["from_at"],
                        parsed["from_at"],
                        parsed["to_at"],
                        parsed["fill_policy"],
                        parsed["missing_policy"],
                        Jsonb(parsed["request_payload"]),
                    ),
                ).fetchone()
                assert build is not None
                resolved_series = _resolve_and_freeze_series(connection, int(build["id"]), parsed)
                _freeze_market_status(connection, int(build["id"]), parsed, resolved_series)
                _freeze_coverage(connection, build, parsed, resolved_series)
                return _build_response(build)
        except (errors.SerializationFailure, errors.UniqueViolation):
            if attempt == 2:
                raise
    raise RuntimeError("데이터셋 빌드 생성 재시도 한도를 초과했다.")


def get_build(repository: object, build_id: int) -> Row | None:
    with _connector(repository)() as connection:
        row = connection.execute("SELECT * FROM dataset_builds WHERE id=%s", (build_id,)).fetchone()
    return None if row is None else _build_response(row)


def get_version(repository: object, dataset_version_id: int) -> Row | None:
    with _connector(repository)() as connection:
        version = connection.execute(
            "SELECT * FROM dataset_versions WHERE id=%s AND sealed_at IS NOT NULL",
            (dataset_version_id,),
        ).fetchone()
        if version is None:
            return None
        rows = connection.execute(
            """
            SELECT id, instrument_id, data_kind, unit,
                   definition_set_hash, calculation_version
            FROM dataset_version_series
            WHERE dataset_version_id=%s
            ORDER BY instrument_id, data_kind, unit, id
            """,
            (dataset_version_id,),
        ).fetchall()
    return {
        "datasetVersionId": int(version["id"]),
        "schemaVersion": version["schema_version"],
        "asOf": version["as_of"],
        "from": version["output_start_at"],
        "to": version["end_at"],
        "contentHash": version["content_hash"],
        "availabilityPolicy": "point_in_time_v1",
        "fillPolicy": version["fill_policy"],
        "missingPolicy": version["missing_policy"],
        "createdAt": version["created_at"],
        "series": [
            {
                "seriesId": int(row["id"]),
                "instrumentId": int(row["instrument_id"]),
                "dataKind": _api_kind(str(row["data_kind"])),
                "unit": row["unit"],
                "definitionSetHash": row["definition_set_hash"],
                "calculationVersion": row["calculation_version"],
            }
            for row in rows
        ],
    }


def list_versions(repository: object, *, page_size: object, cursor: object) -> Row:
    limit = int(cast(int, page_size))
    decoded = _decode_list_cursor(cast(str | None, cursor))
    with _connector(repository)() as connection:
        if decoded is None:
            ceiling_row = connection.execute(
                "SELECT COALESCE(MAX(id),0) AS id FROM dataset_versions WHERE sealed_at IS NOT NULL"
            ).fetchone()
            ceiling = int(ceiling_row["id"])
            last_id = ceiling + 1
        else:
            ceiling = int(cast(int, decoded["ceiling"]))
            last_id = int(cast(int, decoded["lastId"]))
        rows = connection.execute(
            """
            SELECT id FROM dataset_versions
            WHERE sealed_at IS NOT NULL AND id <= %s AND id < %s
            ORDER BY id DESC LIMIT %s
            """,
            (ceiling, last_id, limit + 1),
        ).fetchall()
    page = rows[:limit]
    items = [get_version(repository, int(row["id"])) for row in page]
    return {
        "items": [item for item in items if item is not None],
        "nextCursor": (
            _encode_list_cursor(ceiling, int(page[-1]["id"]))
            if len(rows) > limit and page
            else None
        ),
    }


def get_coverage(repository: object, dataset_version_id: int) -> Row | None:
    with _connector(repository)() as connection:
        version = connection.execute(
            "SELECT coverage_hash FROM dataset_versions WHERE id=%s AND sealed_at IS NOT NULL",
            (dataset_version_id,),
        ).fetchone()
        if version is None:
            return None
        rows = connection.execute(
            """
            SELECT coverage.*, series.id AS series_id
            FROM dataset_version_coverage_snapshots coverage
            JOIN dataset_version_series series
              ON series.id=coverage.dataset_version_series_id
            WHERE coverage.dataset_version_id=%s
            ORDER BY series.id, coverage.range_start_at
            """,
            (dataset_version_id,),
        ).fetchall()
    counts = {quality: 0 for quality in _QUALITY_ORDER}
    requested = 0
    eligible = 0
    for row in rows:
        bucket_count = int(row["expected_count"])
        counts[str(row["status"])] += bucket_count
        requested += bucket_count
        if row["status"] in {"available", "no_trade"}:
            eligible += bucket_count
    return {
        "datasetVersionId": dataset_version_id,
        "snapshotHash": version["coverage_hash"],
        "requestedBucketCount": requested,
        "eligibleBucketCount": eligible,
        "usableRatio": format(Decimal(eligible) / Decimal(requested), "f") if requested else "0",
        "counts": counts,
        "items": [
            {
                "seriesId": int(row["series_id"]),
                "rangeStartAt": row["range_start_at"],
                "rangeEndAt": row["range_end_at"],
                "knowledgeAt": row["knowledge_at"],
                "status": row["status"],
                "bucketCount": int(row["expected_count"]),
            }
            for row in rows
        ],
    }


def get_series(
    repository: object,
    *,
    dataset_version_id: object,
    series_id: object,
    from_at: object,
    to_at: object,
    page_size: object,
    cursor: object,
) -> Row | None:
    version_id = int(cast(int, dataset_version_id))
    resolved_series_id = int(cast(int, series_id))
    start_at = cast(datetime, from_at)
    end_at = cast(datetime, to_at)
    limit = int(cast(int, page_size))
    after = _decode_series_cursor(
        cast(str | None, cursor),
        dataset_version_id=version_id,
        series_id=resolved_series_id,
        from_at=start_at,
        to_at=end_at,
    )
    with _connector(repository)() as connection:
        series = connection.execute(
            """
            SELECT series.* FROM dataset_version_series series
            JOIN dataset_versions version
              ON version.id=series.dataset_version_id AND version.sealed_at IS NOT NULL
            WHERE series.id=%s AND series.dataset_version_id=%s
            """,
            (resolved_series_id, version_id),
        ).fetchone()
        if series is None:
            return None
        rows = _read_series_rows(connection, series, start_at, end_at, after, limit + 1)
    has_more = len(rows) > limit
    page = rows[:limit]
    return {
        "datasetVersionId": version_id,
        "seriesId": resolved_series_id,
        "dataKind": _api_kind(str(series["data_kind"])),
        "unit": series["unit"],
        "items": [_series_point(row) for row in page],
        "nextCursor": _encode_series_cursor(
            dataset_version_id=version_id,
            series_id=resolved_series_id,
            from_at=start_at,
            to_at=end_at,
            last_occurred_at=page[-1]["occurred_at"],
        )
        if has_more and page
        else None,
    }


_PUBLICATION_LEASE_SECONDS = 120
_PUBLICATION_HEARTBEAT_SECONDS = 30


class _DatasetBuildLeaseHeartbeat:
    def __init__(
        self, repository: object, build_id: int, worker_id: str, generation: int
    ) -> None:
        self._repository = repository
        self._build_id = build_id
        self._worker_id = worker_id
        self._generation = generation
        self._stop = Event()
        self._lock = Lock()
        self._thread: Thread | None = None
        self._failure: BaseException | None = None

    def start(self) -> None:
        self.renew()
        self._thread = Thread(
            target=self._run,
            name=f"dataset-build-heartbeat-{self._build_id}",
            daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(_PUBLICATION_HEARTBEAT_SECONDS):
            try:
                self.renew()
            except BaseException as exc:
                self._failure = exc
                self._stop.set()

    def renew(self) -> None:
        self.assert_current()
        with self._lock, _connector(self._repository)() as connection:
            renewed = connection.execute(
                """
                UPDATE dataset_builds
                SET lease_expires_at=clock_timestamp() + make_interval(secs => %s)
                WHERE id=%s AND status='running' AND lease_owner=%s
                  AND lease_generation=%s AND lease_expires_at > clock_timestamp()
                RETURNING id
                """,
                (
                    _PUBLICATION_LEASE_SECONDS,
                    self._build_id,
                    self._worker_id,
                    self._generation,
                ),
            ).fetchone()
        if renewed is None:
            raise DatasetBuildLeaseLostError("데이터셋 빌드 임대 fencing을 잃었다.")

    def assert_current(self) -> None:
        if self._failure is not None:
            if isinstance(self._failure, DatasetBuildLeaseLostError):
                raise self._failure
            raise RuntimeError("데이터셋 빌드 heartbeat가 실패했다.") from self._failure

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            thread = self._thread
            thread.join(timeout=5)
            if thread.is_alive():
                raise RuntimeError("데이터셋 빌드 heartbeat 종료를 확인하지 못했다.")
            self._thread = None
        self.assert_current()


def publish_next_build(repository: object, worker_id: str) -> int:
    connector = getattr(repository, "_connect", None)
    if not callable(connector):
        return 0
    with connector() as connection:
        connection.execute(
            """
            UPDATE dataset_builds SET status='dead_letter', lease_owner=NULL,
              lease_expires_at=NULL, next_retry_at=NULL,
              dead_letter_reason='retry_attempts_exhausted',
              finished_at=clock_timestamp()
            WHERE status='running' AND lease_expires_at <= clock_timestamp()
              AND attempt_count >= max_attempts
            """
        )
        build = connection.execute(
            """
            SELECT * FROM dataset_builds
            WHERE attempt_count < max_attempts AND (
              status='pending'
              OR (status='retry_wait' AND next_retry_at <= clock_timestamp())
              OR (status='running' AND lease_expires_at <= clock_timestamp())
            )
            ORDER BY created_at, id FOR UPDATE SKIP LOCKED LIMIT 1
            """
        ).fetchone()
        if build is None:
            return 0
        generation = int(build["lease_generation"]) + 1
        claimed = connection.execute(
            """
            UPDATE dataset_builds SET status='running', lease_owner=%s,
              lease_expires_at=clock_timestamp() + interval '120 seconds',
              lease_generation=%s, attempt_count=attempt_count+1,
              next_retry_at=NULL, dead_letter_reason=NULL,
              started_at=COALESCE(started_at, clock_timestamp()), finished_at=NULL
            WHERE id=%s AND lease_generation=%s
            RETURNING *
            """,
            (worker_id, generation, build["id"], build["lease_generation"]),
        ).fetchone()
        if claimed is None:
            return 0
    build_id = int(claimed["id"])
    heartbeat = _DatasetBuildLeaseHeartbeat(repository, build_id, worker_id, generation)
    try:
        heartbeat.start()
        with connector() as connection:
            fenced = connection.execute(
                """
                SELECT * FROM dataset_builds
                WHERE id=%s AND status='running' AND lease_owner=%s
                  AND lease_generation=%s AND lease_expires_at > clock_timestamp()
                """,
                (build_id, worker_id, generation),
            ).fetchone()
            if fenced is None:
                return 0
            _publish_claimed_build(connection, fenced, worker_id, generation, heartbeat)
    except DatasetBuildLeaseLostError:
        pass
    except DatasetBuildPublicationError as exc:
        _mark_build_failed(
            repository, build_id, worker_id, generation, "coverage_incomplete", str(exc)
        )
    except Exception as exc:
        _schedule_build_retry(repository, build_id, worker_id, generation, exc)
    finally:
        with suppress(DatasetBuildLeaseLostError, RuntimeError):
            heartbeat.stop()
    return build_id


def _publish_claimed_build(
    connection: Any,
    build: Row,
    worker_id: str,
    generation: int,
    heartbeat: _DatasetBuildLeaseHeartbeat,
) -> None:
    series_rows = connection.execute(
        """
        SELECT series.*, market.exchange, market.market_code
        FROM dataset_build_series series
        JOIN markets market ON market.id=series.market_id
        WHERE series.dataset_build_id=%s
        ORDER BY series.instrument_id, series.data_kind, series.unit, series.id
        """,
        (build["id"],),
    ).fetchall()
    _create_publication_stage(connection)
    for series in series_rows:
        _stage_frozen_members(connection, build, series)
        heartbeat.renew()
    summaries = {
        int(row["build_series_id"]): row
        for row in connection.execute(
            """
            SELECT build_series_id,
              array_agg(DISTINCT definition_hash) FILTER (WHERE definition_hash IS NOT NULL)
                AS definition_hashes,
              array_agg(DISTINCT calculation_version) AS calculation_versions
            FROM dataset_publication_members GROUP BY build_series_id
            """
        ).fetchall()
    }
    for series in series_rows:
        summary = summaries.get(int(series["id"]), {})
        resolved_definitions = set(summary.get("definition_hashes") or ())
        resolved_versions = {str(value) for value in summary.get("calculation_versions") or ()}
        daily_mixed = (
            series["data_kind"] == "candle"
            and series["unit"] == "1d"
            and resolved_versions <= {"source-candle-v1", "candle-rollup-v2"}
        )
        if len(resolved_definitions) > 1 or (len(resolved_versions) > 1 and not daily_mixed):
            raise DatasetBuildPublicationError(
                "한 series에서 둘 이상의 definition/calculation version이 선택되었다."
            )
        if series["definition_set_hash"] is None and resolved_definitions:
            series["definition_set_hash"] = next(iter(resolved_definitions))
        if series["calculation_version"] is None and resolved_versions:
            series["calculation_version"] = next(iter(resolved_versions))
        expected = str(series["calculation_version"])
        if daily_mixed:
            expected = "daily-source-preferred-v1"
        elif resolved_versions and resolved_versions != {expected}:
            raise DatasetBuildPublicationError(
                "calculation_version_mismatch: 선택된 member 계산 버전이 요청과 다르다."
            )
    blocked = connection.execute(
        """
        SELECT EXISTS (
          SELECT 1 FROM dataset_build_coverage_snapshots coverage
          WHERE coverage.dataset_build_id=%s
            AND (
              coverage.status IN ('missing','unavailable','unverified')
              OR (
                coverage.status='available'
                AND (
                  SELECT COUNT(DISTINCT member.occurred_at)
                  FROM dataset_publication_members member
                  WHERE member.build_series_id=coverage.dataset_build_series_id
                    AND member.occurred_at >= coverage.range_start_at
                    AND member.occurred_at < coverage.range_end_at
                    AND member.quality IN ('available','no_trade')
                ) < coverage.expected_count
              )
            )
        ) AS blocked
        """,
        (build["id"],),
    ).fetchone()
    if build["missing_policy"] == "fail" and blocked["blocked"]:
        raise DatasetBuildPublicationError(
            "missing_policy=fail 조건에서 불완전한 coverage가 발견되었다."
        )
    specification = DatasetCanonicalSpecification(
        schema_version=str(build["schema_version"]),
        as_of=build["as_of"],
        input_start_at=build["input_start_at"],
        output_start_at=build["output_start_at"],
        end_at=build["end_at"],
        series=tuple(
            DatasetSeriesRequest(
                instrument_id=int(row["instrument_id"]),
                exchange=str(row["exchange"]),
                market_code=str(row["market_code"]),
                data_kind=cast(Any, row["data_kind"]),
                unit=str(row["unit"]),
                definition_set_hash=row["definition_set_hash"],
                calculation_version=row["calculation_version"],
            )
            for row in series_rows
        ),
        fill_policy=cast(Any, build["fill_policy"]),
        missing_policy=cast(Any, build["missing_policy"]),
        ordering_policy=str(build["ordering_policy"]),
    )
    hashes, series_digests = _stream_publication_hashes(
        connection, int(build["id"]), specification, series_rows, heartbeat
    )
    inserted = connection.execute(
        """
        INSERT INTO dataset_versions (
          schema_version, as_of, input_start_at, output_start_at, end_at,
          fill_policy, missing_policy, ordering_policy,
          selection_hash, manifest_hash, market_status_hash, coverage_hash, content_hash
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (content_hash) DO NOTHING RETURNING *
        """,
        (
            build["schema_version"],
            build["as_of"],
            build["input_start_at"],
            build["output_start_at"],
            build["end_at"],
            build["fill_policy"],
            build["missing_policy"],
            build["ordering_policy"],
            hashes.selection_hash,
            hashes.manifest_hash,
            hashes.market_status_hash,
            hashes.coverage_hash,
            hashes.content_hash,
        ),
    ).fetchone()
    if inserted is None:
        inserted = connection.execute(
            "SELECT * FROM dataset_versions WHERE content_hash=%s AND sealed_at IS NOT NULL",
            (hashes.content_hash,),
        ).fetchone()
        if inserted is None:
            raise errors.SerializationFailure(
                "동시 content-address version을 현재 snapshot에서 볼 수 없다."
            )
    else:
        _insert_version_contents(connection, inserted, series_rows, series_digests, int(build["id"]))
        heartbeat.renew()
        sealed = connection.execute(
            """
            UPDATE dataset_versions SET sealed_at=clock_timestamp()
            WHERE id=%s AND sealed_at IS NULL RETURNING id
            """,
            (inserted["id"],),
        ).fetchone()
        if sealed is None:
            raise errors.SerializationFailure("dataset version 봉인에 실패했다.")
    heartbeat.stop()
    updated = connection.execute(
        """
        UPDATE dataset_builds SET status='succeeded', dataset_version_id=%s,
          lease_owner=NULL, lease_expires_at=NULL, finished_at=clock_timestamp()
        WHERE id=%s AND status='running' AND lease_owner=%s AND lease_generation=%s
          AND lease_expires_at > clock_timestamp()
        RETURNING id
        """,
        (inserted["id"], build["id"], worker_id, generation),
    ).fetchone()
    if updated is None:
        raise errors.SerializationFailure("데이터셋 빌드 lease generation fencing에 실패했다.")


def _parse_request(**arguments: object) -> Row:
    request_id = str(arguments["request_id"]).strip()
    idempotency_key = str(arguments["idempotency_key"]).strip()
    actor_id = str(arguments["actor_id"]).strip()
    reason = str(arguments["reason"]).strip()
    requested_at = cast(datetime, arguments["requested_at"])
    selection = cast(Mapping[str, object], arguments["selection"])
    policies = cast(Mapping[str, object], arguments["policies"])
    as_of = cast(datetime, selection["asOf"])
    from_at = cast(datetime, selection["from"])
    to_at = cast(datetime, selection["to"])
    raw_series = cast(Sequence[Mapping[str, object]], selection["series"])
    series = [
        {
            "instrument_id": int(cast(int, item["instrumentId"])),
            "data_kind": _store_kind(str(item["dataKind"])),
            "unit": str(item["unit"]),
            "definition_set_hash": item.get("definitionSetHash"),
            "calculation_version": _resolve_calculation_version(
                _store_kind(str(item["dataKind"])),
                str(item["unit"]),
                cast(str | None, item.get("calculationVersion")),
            ),
        }
        for item in raw_series
    ]
    fill_policy = str(policies["fillPolicy"])
    missing_policy = str(policies["missingPolicy"])
    validate_dataset_policies(
        series=tuple(
            DatasetSeriesRequest(
                instrument_id=cast(int, item["instrument_id"]),
                exchange="PENDING",
                market_code="PENDING",
                data_kind=cast(Any, item["data_kind"]),
                unit=cast(str, item["unit"]),
            )
            for item in series
        ),
        fill_policy=fill_policy,
        missing_policy=missing_policy,
    )
    for name, value in (
        ("requestedAt", requested_at),
        ("asOf", as_of),
        ("from", from_at),
        ("to", to_at),
    ):
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError(f"{name}은 UTC timezone-aware datetime이어야 한다.")
    if not request_id or not idempotency_key or not actor_id or not reason:
        raise ValueError("감사 명령 문자열은 비어 있을 수 없다.")
    if not from_at < to_at <= as_of <= requested_at:
        raise ValueError("데이터셋 요청은 from < to <= asOf <= requestedAt이어야 한다.")
    if policies.get("availabilityPolicy") != "point_in_time_v1":
        raise ValueError("availabilityPolicy는 point_in_time_v1이어야 한다.")
    request_payload = _jsonable(
        {
            "requestId": request_id,
            "idempotencyKey": idempotency_key,
            "actorId": actor_id,
            "requestedAt": requested_at,
            "reason": reason,
            "selection": selection,
            "policies": policies,
        }
    )
    return {
        "request_id": request_id,
        "idempotency_key": idempotency_key,
        "actor_id": actor_id,
        "requested_at": requested_at,
        "reason": reason,
        "as_of": as_of,
        "from_at": from_at,
        "to_at": to_at,
        "series": series,
        "fill_policy": fill_policy,
        "missing_policy": missing_policy,
        "request_payload": request_payload,
        "request_hash": canonical_payload_hash(cast(Any, request_payload)),
    }


def _resolve_calculation_version(
    data_kind: str, unit: str, requested: str | None
) -> str:
    if data_kind == "indicator":
        expected = "indicator-v1"
    elif data_kind == "market_statistic":
        expected = "market-statistics-v1"
    elif data_kind == "microstructure":
        expected = "microstructure-v1"
    elif unit == "1m":
        expected = "source-candle-v1"
    elif unit == "1d":
        expected = "daily-source-preferred-v1"
    else:
        expected = "candle-rollup-v2"
    if requested is not None and requested != expected:
        raise ValueError(
            "calculation_version_mismatch: "
            f"{data_kind}/{unit}에는 {expected}만 허용되지만 {requested}가 요청되었다."
        )
    return expected


def _resolve_and_freeze_series(connection: Any, build_id: int, parsed: Row) -> list[Row]:
    resolved: list[Row] = []
    for item in parsed["series"]:
        market = connection.execute(
            """
            SELECT instrument.id AS instrument_id, market.id AS market_id,
                   market.exchange, market.market_code
            FROM instruments instrument
            JOIN markets market
              ON market.exchange=instrument.exchange
             AND market.market_code=instrument.market_code
            WHERE instrument.id=%s
            """,
            (item["instrument_id"],),
        ).fetchone()
        if market is None:
            raise ValueError(f"시장 자연키를 찾을 수 없는 instrument다: {item['instrument_id']}")
        ceilings = connection.execute(
            """
            SELECT
              (SELECT MAX(id) FROM source_candle_revisions WHERE instrument_id=%s AND knowledge_at <= %s) AS source_revision_through_id,
              (SELECT MAX(id) FROM candle_rollups WHERE instrument_id=%s AND knowledge_at <= %s) AS candle_rollup_through_id,
              (SELECT MAX(event.id) FROM data_quality_events event JOIN collection_target_specs spec ON spec.id=event.target_spec_id WHERE spec.market_id=%s AND event.detected_at <= %s) AS quality_event_through_id,
              (SELECT MAX(id) FROM indicator_materializations WHERE instrument_id=%s AND knowledge_at <= %s) AS indicator_materialization_through_id,
              (SELECT MAX(id) FROM market_statistics WHERE instrument_id=%s AND knowledge_at <= %s) AS market_statistic_through_id,
              (SELECT MAX(id) FROM microstructure_materializations WHERE instrument_id=%s AND knowledge_at <= %s) AS microstructure_materialization_through_id,
              (SELECT MAX(id) FROM market_status_history WHERE market_id=%s AND observed_at <= %s) AS market_status_history_through_id,
              (SELECT MAX(snapshot.id) FROM orderbook_snapshots snapshot
                 JOIN source_receipts receipt ON receipt.id=snapshot.source_receipt_id
                WHERE snapshot.instrument_id=%s AND receipt.knowledge_at <= %s)
                AS orderbook_snapshot_through_id,
              (SELECT MAX(trade.id) FROM trade_events trade
                 JOIN source_receipts receipt ON receipt.id=trade.source_receipt_id
                WHERE trade.instrument_id=%s AND receipt.knowledge_at <= %s)
                AS trade_event_through_id,
              (SELECT MAX(id) FROM source_receipts
                WHERE instrument_id=%s AND knowledge_at <= %s) AS source_receipt_through_id,
              (SELECT MAX(id) FROM realtime_connection_quality_intervals WHERE market_id=%s AND detected_at <= %s) AS connection_quality_through_id
            """,
            (
                item["instrument_id"],
                parsed["as_of"],
                item["instrument_id"],
                parsed["as_of"],
                market["market_id"],
                parsed["as_of"],
                item["instrument_id"],
                parsed["as_of"],
                item["instrument_id"],
                parsed["as_of"],
                item["instrument_id"],
                parsed["as_of"],
                market["market_id"],
                parsed["as_of"],
                item["instrument_id"],
                parsed["as_of"],
                item["instrument_id"],
                parsed["as_of"],
                item["instrument_id"],
                parsed["as_of"],
                market["market_id"],
                parsed["as_of"],
            ),
        ).fetchone()
        assert ceilings is not None
        quality_ceilings = []
        for quality_data_type, quality_unit in _quality_authorities(item):
            quality_ceiling = connection.execute(
                """
                SELECT MAX(event.id) AS id
                FROM data_quality_events event
                JOIN collection_target_specs spec ON spec.id=event.target_spec_id
                WHERE spec.market_id=%s AND spec.data_type=%s
                  AND spec.candle_unit IS NOT DISTINCT FROM %s
                  AND event.detected_at <= %s
                """,
                (market["market_id"], quality_data_type, quality_unit, parsed["as_of"]),
            ).fetchone()
            if quality_ceiling["id"] is not None:
                quality_ceilings.append(int(quality_ceiling["id"]))
        ceilings["quality_event_through_id"] = max(quality_ceilings, default=None)
        row = connection.execute(
            """
            INSERT INTO dataset_build_series (
              dataset_build_id, market_id, instrument_id, data_kind, unit,
              definition_set_hash, calculation_version, fill_policy,
              source_revision_through_id, candle_rollup_through_id,
              quality_event_through_id, indicator_materialization_through_id,
              market_statistic_through_id, microstructure_materialization_through_id,
              market_status_history_through_id, orderbook_snapshot_through_id,
              trade_event_through_id, source_receipt_through_id,
              connection_quality_through_id
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING *
            """,
            (
                build_id,
                market["market_id"],
                item["instrument_id"],
                item["data_kind"],
                item["unit"],
                item["definition_set_hash"],
                item["calculation_version"],
                parsed["fill_policy"],
                ceilings["source_revision_through_id"],
                ceilings["candle_rollup_through_id"],
                ceilings["quality_event_through_id"],
                ceilings["indicator_materialization_through_id"],
                ceilings["market_statistic_through_id"],
                ceilings["microstructure_materialization_through_id"],
                ceilings["market_status_history_through_id"],
                ceilings["orderbook_snapshot_through_id"],
                ceilings["trade_event_through_id"],
                ceilings["source_receipt_through_id"],
                ceilings["connection_quality_through_id"],
            ),
        ).fetchone()
        assert row is not None
        resolved.append({**row, **market})
    return resolved


def _quality_authorities(
    series: Mapping[str, object],
) -> tuple[tuple[str, str | None], ...]:
    if series["data_kind"] == "microstructure":
        return (("orderbook_snapshot", None), ("trade_event", None))
    # 수집 계약의 source_candle 권위 단위는 직접 수집하는 1m/1d뿐이다.
    authority_unit = "1d" if series["data_kind"] == "candle" and series["unit"] == "1d" else "1m"
    return (("source_candle", authority_unit),)


def _freeze_market_status(
    connection: Any, build_id: int, parsed: Row, series: Sequence[Row]
) -> None:
    market_ids = sorted({int(item["market_id"]) for item in series})
    for market_id in market_ids:
        rows = connection.execute(
            """
            SELECT history.*, market.exchange, market.market_code
            FROM market_status_history history
            JOIN markets market ON market.id=history.market_id
            WHERE history.market_id=%s AND history.observed_at <= %s
              AND history.valid_from < %s
              AND COALESCE(history.valid_to, 'infinity'::timestamptz) > %s
            ORDER BY history.valid_from, history.id
            """,
            (market_id, parsed["as_of"], parsed["to_at"], parsed["from_at"]),
        ).fetchall()
        if not rows:
            raise ValueError(f"asOf 시점의 시장 상태가 없다: market_id={market_id}")
        for row in rows:
            valid_from = max(row["valid_from"], parsed["from_at"])
            valid_to = min(row["valid_to"] or parsed["to_at"], parsed["to_at"])
            event_hash = canonical_payload_hash(cast(Any, row["market_event"]))
            snapshot_hash = canonical_payload_hash(
                {
                    "exchange": row["exchange"],
                    "marketCode": row["market_code"],
                    "tradingStatus": row["trading_status"],
                    "marketWarning": row["market_warning"],
                    "eventHash": event_hash,
                    "sourcePayloadChecksum": row["source_payload_checksum"],
                    "validFrom": valid_from,
                    "validTo": valid_to,
                    "observedAt": row["observed_at"],
                }
            )
            connection.execute(
                """
                INSERT INTO dataset_build_market_status_snapshots (
                  dataset_build_id, source_market_status_history_id, market_id,
                  exchange, market_code, trading_status, market_warning, market_event,
                  source_payload_checksum, valid_from, valid_to, observed_at, snapshot_hash
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    build_id,
                    row["id"],
                    market_id,
                    row["exchange"],
                    row["market_code"],
                    row["trading_status"],
                    row["market_warning"],
                    Jsonb(row["market_event"]),
                    row["source_payload_checksum"],
                    valid_from,
                    valid_to,
                    row["observed_at"],
                    snapshot_hash,
                ),
            )


def _freeze_coverage(connection: Any, build: Row, parsed: Row, series: Sequence[Row]) -> None:
    build_id = int(build["id"])
    for item in series:
        events: list[Row] = []
        for quality_data_type, quality_unit in _quality_authorities(item):
            events.extend(
                connection.execute(
                    """
                    SELECT event.*, spec.data_type AS authority_data_type,
                           spec.candle_unit AS authority_unit
                    FROM data_quality_events event
                    JOIN collection_target_specs spec ON spec.id=event.target_spec_id
                    WHERE spec.market_id=%s AND spec.data_type=%s
                      AND spec.candle_unit IS NOT DISTINCT FROM %s
                      AND event.detected_at <= %s
                      AND (%s::bigint IS NULL OR event.id <= %s)
                      AND tstzrange(event.range_start_at,event.range_end_at,'[)')
                          && tstzrange(%s,%s,'[)')
                    ORDER BY event.id
                    """,
                    (
                        item["market_id"],
                        quality_data_type,
                        quality_unit,
                        parsed["as_of"],
                        item["quality_event_through_id"],
                        item["quality_event_through_id"],
                        parsed["from_at"],
                        parsed["to_at"],
                    ),
                ).fetchall()
            )
        statuses = connection.execute(
            """
            SELECT * FROM dataset_build_market_status_snapshots
            WHERE dataset_build_id=%s AND market_id=%s
              AND valid_from < %s
              AND COALESCE(valid_to, 'infinity'::timestamptz) > %s
            ORDER BY valid_from, id
            """,
            (build_id, item["market_id"], parsed["to_at"], parsed["from_at"]),
        ).fetchall()
        for range_start, range_end, event, market_status in _project_coverage_ranges(
            events, statuses, parsed["from_at"], parsed["to_at"]
        ):
            expected = _expected_bucket_count(range_start, range_end, str(item["unit"]))
            observed = _frozen_member_count(connection, parsed, item, range_start, range_end)
            market_available = (
                market_status is not None and market_status["trading_status"] == "active"
            )
            if not market_available:
                status = "unavailable"
            elif event:
                status = _normalize_source_quality(event["new_status"])
            else:
                status = "available" if observed >= expected > 0 else "unverified"
            if event and not market_available and market_status:
                knowledge_at = max(event["combined_detected_at"], market_status["observed_at"])
            elif event:
                knowledge_at = event["combined_detected_at"]
            elif not market_available and market_status:
                knowledge_at = market_status["observed_at"]
            else:
                knowledge_at = build["frozen_at"]
            evidence_hash = canonical_payload_hash(
                {
                    "exchange": item["exchange"],
                    "marketCode": item["market_code"],
                    "dataKind": item["data_kind"],
                    "unit": item["unit"],
                    "definitionSetHash": item["definition_set_hash"],
                    "calculationVersion": item["calculation_version"],
                    "rangeStartAt": range_start,
                    "rangeEndAt": range_end,
                    "knowledgeAt": knowledge_at,
                    "status": status,
                    "observedCount": observed,
                    "expectedCount": expected,
                    "sourceEvidence": event["authority_evidence"] if event else [],
                    "marketStatusSnapshotHash": (
                        market_status["snapshot_hash"] if market_status else None
                    ),
                }
            )
            connection.execute(
                """
                INSERT INTO dataset_build_coverage_snapshots (
                  dataset_build_id, dataset_build_series_id, source_data_quality_event_id,
                  exchange, market_code, data_kind, unit,
                  definition_set_hash, calculation_version, range_start_at, range_end_at,
                  knowledge_at, status, observed_count, expected_count, evidence_hash
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    build_id,
                    item["id"],
                    event["id"] if event else None,
                    item["exchange"],
                    item["market_code"],
                    item["data_kind"],
                    item["unit"],
                    item["definition_set_hash"],
                    item["calculation_version"],
                    range_start,
                    range_end,
                    knowledge_at,
                    status,
                    observed,
                    expected,
                    evidence_hash,
                ),
            )


def _frozen_member_count(
    connection: Any,
    parsed: Row,
    series: Row,
    range_start_at: datetime,
    range_end_at: datetime,
) -> int:
    if series["data_kind"] == "candle" and series["unit"] == "1d":
        row = connection.execute(
            """
            SELECT COUNT(DISTINCT candle_start_at) AS count FROM (
              SELECT candle_start_at FROM source_candle_revisions
              WHERE instrument_id=%s AND candle_unit='1d'
                AND candle_start_at >= %s AND candle_start_at < %s
                AND knowledge_at <= %s
                AND (%s::bigint IS NULL OR id <= %s)
              UNION ALL
              SELECT candle_start_at FROM candle_rollups
              WHERE instrument_id=%s AND candle_unit='1d'
                AND candle_start_at >= %s AND candle_start_at < %s
                AND knowledge_at <= %s
                AND (%s::bigint IS NULL OR id <= %s)
            ) daily
            """,
            (
                series["instrument_id"],
                range_start_at,
                range_end_at,
                parsed["as_of"],
                series["source_revision_through_id"],
                series["source_revision_through_id"],
                series["instrument_id"],
                range_start_at,
                range_end_at,
                parsed["as_of"],
                series["candle_rollup_through_id"],
                series["candle_rollup_through_id"],
            ),
        ).fetchone()
        return int(row["count"])
    table, time_column, ceiling_column = {
        "candle": (
            "source_candle_revisions" if series["unit"] in {"1m", "1d"} else "candle_rollups",
            "candle_start_at",
            "source_revision_through_id"
            if series["unit"] in {"1m", "1d"}
            else "candle_rollup_through_id",
        ),
        "indicator": (
            "indicator_materializations",
            "occurred_at",
            "indicator_materialization_through_id",
        ),
        "market_statistic": ("market_statistics", "occurred_at", "market_statistic_through_id"),
        "microstructure": (
            "microstructure_materializations",
            "bucket_start_at",
            "microstructure_materialization_through_id",
        ),
    }[str(series["data_kind"])]
    ceiling = series[ceiling_column]
    if ceiling is None:
        return 0
    row = connection.execute(
        f"""SELECT COUNT(DISTINCT {time_column}) AS count FROM {table}
            WHERE instrument_id=%s AND {time_column} >= %s AND {time_column} < %s
              AND knowledge_at <= %s AND id <= %s""",
        (
            series["instrument_id"],
            range_start_at,
            range_end_at,
            parsed["as_of"],
            ceiling,
        ),
    ).fetchone()
    return int(row["count"])


def _project_quality_event_ranges(
    events: Sequence[Row], start_at: datetime, end_at: datetime
) -> list[tuple[datetime, datetime, Row | None]]:
    boundaries = {start_at, end_at}
    for event in events:
        if event["range_start_at"] < end_at and event["range_end_at"] > start_at:
            boundaries.add(max(start_at, event["range_start_at"]))
            boundaries.add(min(end_at, event["range_end_at"]))
    ordered = sorted(boundaries)
    projected: list[tuple[datetime, datetime, Row | None]] = []
    for range_start, range_end in pairwise(ordered):
        covering = [
            event
            for event in events
            if event["range_start_at"] <= range_start and event["range_end_at"] >= range_end
        ]
        projected.append(
            (range_start, range_end, max(covering, key=lambda row: int(row["id"]), default=None))
        )
    return projected


def _project_coverage_ranges(
    events: Sequence[Row],
    statuses: Sequence[Row],
    start_at: datetime,
    end_at: datetime,
) -> list[tuple[datetime, datetime, Row | None, Row | None]]:
    boundaries = {start_at, end_at}
    for row, start_name, end_name in (
        *((event, "range_start_at", "range_end_at") for event in events),
        *((status, "valid_from", "valid_to") for status in statuses),
    ):
        row_end = row[end_name] or end_at
        if row[start_name] < end_at and row_end > start_at:
            boundaries.add(max(start_at, row[start_name]))
            boundaries.add(min(end_at, row_end))
    projected: list[tuple[datetime, datetime, Row | None, Row | None]] = []
    for range_start, range_end in pairwise(sorted(boundaries)):
        latest_by_authority: dict[tuple[object, object], Row] = {}
        for row in events:
            if row["range_start_at"] <= range_start and row["range_end_at"] >= range_end:
                authority = (row["authority_data_type"], row["authority_unit"])
                current = latest_by_authority.get(authority)
                if current is None or int(row["id"]) > int(current["id"]):
                    latest_by_authority[authority] = row
        event: Row | None = None
        if latest_by_authority:
            event = dict(
                max(
                    latest_by_authority.values(),
                    key=lambda row: (
                        _QUALITY_ORDER[_normalize_source_quality(row["new_status"])],
                        int(row["id"]),
                    ),
                )
            )
            latest_events = sorted(
                latest_by_authority.values(),
                key=lambda row: (str(row["authority_data_type"]), str(row["authority_unit"])),
            )
            event["combined_detected_at"] = max(row["detected_at"] for row in latest_events)
            event["authority_evidence"] = [
                {
                    "dataType": row["authority_data_type"],
                    "unit": row["authority_unit"],
                    "eventId": int(row["id"]),
                    "status": _normalize_source_quality(row["new_status"]),
                    "evidence": row["evidence"],
                }
                for row in latest_events
            ]
        market_status = max(
            (
                row
                for row in statuses
                if row["valid_from"] <= range_start
                and (row["valid_to"] is None or row["valid_to"] >= range_end)
            ),
            key=lambda row: (row["observed_at"], int(row["id"])),
            default=None,
        )
        projected.append((range_start, range_end, event, market_status))
    return projected


MAX_PUBLICATION_CHUNK_SIZE = 4096


def _create_publication_stage(connection: Any) -> None:
    connection.execute(
        """
        CREATE TEMP TABLE dataset_publication_members (
          build_series_id BIGINT NOT NULL,
          instrument_id BIGINT NOT NULL,
          data_kind TEXT NOT NULL,
          exchange TEXT NOT NULL,
          market_code TEXT NOT NULL,
          unit TEXT NOT NULL,
          source_ref_id BIGINT NOT NULL,
          source_kind TEXT NOT NULL,
          occurred_at TIMESTAMPTZ NOT NULL,
          knowledge_at TIMESTAMPTZ NOT NULL,
          source_as_of TIMESTAMPTZ NOT NULL,
          content_hash TEXT NOT NULL,
          quality TEXT NOT NULL,
          calculation_version TEXT NOT NULL,
          definition_hash TEXT
        ) ON COMMIT DROP
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX dataset_publication_members_series_time_uq
          ON dataset_publication_members (build_series_id, occurred_at)
        """
    )


def _stage_projection(
    connection: Any,
    series: Row,
    projection_sql: str,
    parameters: Sequence[object],
    *,
    source_kind_sql: str,
) -> None:
    connection.execute(
        f"""
        INSERT INTO dataset_publication_members (
          build_series_id, instrument_id, data_kind, exchange, market_code, unit,
          source_ref_id, source_kind, occurred_at, knowledge_at, source_as_of,
          content_hash, quality, calculation_version, definition_hash
        )
        SELECT %s,%s,%s,%s,%s,%s, projection.source_ref_id, {source_kind_sql},
          projection.occurred_at, projection.knowledge_at, projection.source_as_of,
          projection.content_hash, projection.quality, projection.calculation_version,
          projection.definition_hash
        FROM ({projection_sql}) projection
        """,
        (
            series["id"],
            series["instrument_id"],
            series["data_kind"],
            series["exchange"],
            series["market_code"],
            series["unit"],
            *parameters,
        ),
    )


def _stage_frozen_members(connection: Any, build: Row, series: Row) -> None:
    kind = str(series["data_kind"])
    if kind == "candle":
        _stage_candles(connection, build, series)
        return
    if kind == "indicator":
        _stage_projection(
            connection,
            series,
            """
            SELECT * FROM (
              SELECT materialization.id AS source_ref_id, materialization.occurred_at,
                materialization.knowledge_at, materialization.source_as_of,
                materialization.content_hash,
                CASE WHEN materialization.calculation_status='ready' THEN 'available' ELSE 'missing' END AS quality,
                'indicator-v1' AS calculation_version,
                materialization.definition_set_hash AS definition_hash,
                ROW_NUMBER() OVER (PARTITION BY materialization.occurred_at
                  ORDER BY materialization.knowledge_at DESC, materialization.id DESC) AS projection_rank
              FROM indicator_materializations materialization
              WHERE materialization.instrument_id=%s AND materialization.candle_unit=%s
                AND materialization.occurred_at >= %s AND materialization.occurred_at < %s
                AND materialization.knowledge_at <= %s AND materialization.id <= %s
                AND (%s::text IS NULL OR materialization.definition_set_hash=%s)
            ) projection WHERE projection_rank=1 ORDER BY occurred_at
            """,
            (
                series["instrument_id"],
                series["unit"],
                build["output_start_at"],
                build["end_at"],
                build["as_of"],
                series["indicator_materialization_through_id"],
                series["definition_set_hash"],
                series["definition_set_hash"],
            ),
            source_kind_sql="'indicator'::text",
        )
        return
    if kind == "market_statistic":
        _stage_projection(
            connection,
            series,
            """
            SELECT * FROM (
              SELECT statistic.id AS source_ref_id, statistic.occurred_at,
                statistic.knowledge_at, statistic.source_as_of, statistic.content_hash,
                CASE WHEN statistic.return_status='ready' AND statistic.volatility_status='ready'
                           AND statistic.trade_status='ready' THEN 'available' ELSE 'missing' END AS quality,
                statistic.calculation_version, NULL::text AS definition_hash,
                ROW_NUMBER() OVER (PARTITION BY statistic.occurred_at
                  ORDER BY statistic.knowledge_at DESC, statistic.id DESC) AS projection_rank
              FROM market_statistics statistic
              WHERE statistic.instrument_id=%s AND statistic.interval=%s
                AND statistic.occurred_at >= %s AND statistic.occurred_at < %s
                AND statistic.knowledge_at <= %s AND statistic.id <= %s
                AND (%s::text IS NULL OR statistic.calculation_version=%s)
            ) projection WHERE projection_rank=1 ORDER BY occurred_at
            """,
            (
                series["instrument_id"],
                series["unit"],
                build["output_start_at"],
                build["end_at"],
                build["as_of"],
                series["market_statistic_through_id"],
                series["calculation_version"],
                series["calculation_version"],
            ),
            source_kind_sql="'market_statistic'::text",
        )
        return
    _stage_projection(
        connection,
        series,
        """
        SELECT * FROM (
          SELECT materialization.id AS source_ref_id,
            materialization.bucket_start_at AS occurred_at,
            materialization.knowledge_at, materialization.source_as_of,
            statistic.content_hash,
            CASE
              WHEN (statistic.orderbook_status <> 'ready'
                    AND statistic.orderbook_quality <> 'no_trade')
                OR (statistic.trade_status <> 'ready'
                    AND statistic.trade_quality <> 'no_trade') THEN 'missing'
              WHEN statistic.orderbook_quality='unverified' OR statistic.trade_quality='unverified' THEN 'unverified'
              WHEN statistic.orderbook_quality='unavailable' OR statistic.trade_quality='unavailable' THEN 'unavailable'
              WHEN statistic.orderbook_quality='missing' OR statistic.trade_quality='missing' THEN 'missing'
              WHEN statistic.orderbook_quality='no_trade' AND statistic.trade_quality='no_trade' THEN 'no_trade'
              ELSE 'available' END AS quality,
            definition.calculation_version, definition.definition_hash,
            ROW_NUMBER() OVER (PARTITION BY materialization.bucket_start_at
              ORDER BY materialization.knowledge_at DESC, materialization.id DESC) AS projection_rank
          FROM microstructure_materializations materialization
          JOIN microstructure_definition_versions definition ON definition.id=materialization.definition_version_id
          JOIN microstructure_statistics statistic ON statistic.materialization_id=materialization.id
          WHERE materialization.instrument_id=%s
            AND materialization.bucket_start_at >= %s AND materialization.bucket_start_at < %s
            AND materialization.knowledge_at <= %s AND materialization.id <= %s
            AND (%s::text IS NULL OR definition.calculation_version=%s)
            AND (%s::text IS NULL OR definition.definition_hash=%s)
        ) projection WHERE projection_rank=1 ORDER BY occurred_at
        """,
        (
            series["instrument_id"],
            build["output_start_at"],
            build["end_at"],
            build["as_of"],
            series["microstructure_materialization_through_id"],
            series["calculation_version"],
            series["calculation_version"],
            series["definition_set_hash"],
            series["definition_set_hash"],
        ),
        source_kind_sql="'microstructure'::text",
    )


def _stage_candles(connection: Any, build: Row, series: Row) -> None:
    if series["unit"] == "1d":
        _stage_daily_candles(connection, build, series)
        return
    if series["unit"] == "1m":
        _stage_projection(
            connection,
            series,
            """
            SELECT * FROM (
              SELECT revision.id AS source_ref_id, revision.candle_start_at AS occurred_at,
                revision.knowledge_at, revision.source_as_of,
                revision.input_content_hash AS content_hash,
                CASE COALESCE(quality.new_status, 'observed')
                  WHEN 'observed' THEN 'available' WHEN 'failed' THEN 'missing'
                  ELSE quality.new_status END AS quality,
                'source-candle-v1' AS calculation_version, NULL::text AS definition_hash,
                'source'::text AS source_kind,
                ROW_NUMBER() OVER (PARTITION BY revision.candle_start_at
                  ORDER BY revision.source_as_of DESC, revision.revision_number DESC, revision.id DESC) AS projection_rank
              FROM source_candle_revisions revision
              LEFT JOIN LATERAL (
                SELECT event.new_status FROM data_quality_events event
                JOIN collection_target_specs spec ON spec.id=event.target_spec_id
                WHERE spec.market_id=revision.market_id
                  AND spec.data_type='source_candle'
                  AND spec.candle_unit=revision.candle_unit
                  AND event.detected_at <= %s
                  AND %s::bigint IS NOT NULL AND event.id <= %s
                  AND tstzrange(event.range_start_at,event.range_end_at,'[)') @> revision.candle_start_at
                ORDER BY event.detected_at DESC, event.id DESC LIMIT 1
              ) quality ON TRUE
              WHERE revision.instrument_id=%s AND revision.candle_unit=%s
                AND revision.candle_start_at >= %s AND revision.candle_start_at < %s
                AND revision.knowledge_at <= %s AND revision.id <= %s
            ) projection WHERE projection_rank=1 ORDER BY occurred_at
            """,
            (
                build["as_of"],
                series["quality_event_through_id"],
                series["quality_event_through_id"],
                series["instrument_id"],
                series["unit"],
                build["input_start_at"],
                build["end_at"],
                build["as_of"],
                series["source_revision_through_id"],
            ),
            source_kind_sql="projection.source_kind",
        )
        return
    _stage_projection(
        connection,
        series,
        """
        SELECT * FROM (
          SELECT rollup.id AS source_ref_id, rollup.candle_start_at AS occurred_at,
            rollup.knowledge_at, rollup.source_as_of,
            rollup.result_content_hash AS content_hash, rollup.quality,
            rollup.calculation_version, NULL::text AS definition_hash,
            'rollup'::text AS source_kind,
            ROW_NUMBER() OVER (PARTITION BY rollup.candle_start_at
              ORDER BY rollup.source_revision_through_id DESC,
                rollup.quality_event_through_id DESC NULLS LAST,
                rollup.knowledge_at DESC, rollup.id DESC) AS projection_rank
          FROM candle_rollups rollup
          WHERE rollup.instrument_id=%s AND rollup.candle_unit=%s
            AND rollup.candle_start_at >= %s AND rollup.candle_start_at < %s
            AND rollup.knowledge_at <= %s AND rollup.id <= %s
            AND rollup.calculation_version='candle-rollup-v2'
        ) projection WHERE projection_rank=1 ORDER BY occurred_at
        """,
        (
            series["instrument_id"],
            series["unit"],
            build["input_start_at"],
            build["end_at"],
            build["as_of"],
            series["candle_rollup_through_id"],
        ),
        source_kind_sql="projection.source_kind",
    )


def _stage_daily_candles(connection: Any, build: Row, series: Row) -> None:
    _stage_projection(
        connection,
        series,
        """
        WITH candidates AS (
          SELECT revision.id AS source_ref_id, revision.candle_start_at AS occurred_at,
            revision.knowledge_at, revision.source_as_of,
            revision.input_content_hash AS content_hash,
            CASE COALESCE(quality.new_status, 'observed')
              WHEN 'observed' THEN 'available' WHEN 'failed' THEN 'missing'
              ELSE quality.new_status END AS quality,
            'source-candle-v1' AS calculation_version,
            NULL::text AS definition_hash, 'source'::text AS source_kind, 0 AS priority
          FROM source_candle_revisions revision
          LEFT JOIN LATERAL (
            SELECT event.new_status FROM data_quality_events event
            JOIN collection_target_specs spec ON spec.id=event.target_spec_id
            WHERE spec.market_id=revision.market_id
              AND spec.data_type='source_candle' AND spec.candle_unit='1d'
              AND event.detected_at <= %s
              AND %s::bigint IS NOT NULL AND event.id <= %s
              AND tstzrange(event.range_start_at,event.range_end_at,'[)')
                  @> revision.candle_start_at
            ORDER BY event.detected_at DESC, event.id DESC LIMIT 1
          ) quality ON TRUE
          WHERE revision.instrument_id=%s AND revision.candle_unit='1d'
            AND revision.candle_start_at >= %s AND revision.candle_start_at < %s
            AND revision.knowledge_at <= %s
            AND %s::bigint IS NOT NULL AND revision.id <= %s
          UNION ALL
          SELECT rollup.id, rollup.candle_start_at, rollup.knowledge_at,
            rollup.source_as_of, rollup.result_content_hash, rollup.quality,
            rollup.calculation_version, NULL::text, 'rollup'::text, 1
          FROM candle_rollups rollup
          WHERE rollup.instrument_id=%s AND rollup.candle_unit='1d'
            AND rollup.candle_start_at >= %s AND rollup.candle_start_at < %s
            AND rollup.knowledge_at <= %s
            AND %s::bigint IS NOT NULL AND rollup.id <= %s
            AND rollup.calculation_version='candle-rollup-v2'
        )
        SELECT * FROM (
          SELECT candidates.*, ROW_NUMBER() OVER (
            PARTITION BY occurred_at
            ORDER BY priority, knowledge_at DESC, source_ref_id DESC
          ) AS projection_rank
          FROM candidates
        ) projection WHERE projection_rank=1 ORDER BY occurred_at
        """,
        (
            build["as_of"],
            series["quality_event_through_id"],
            series["quality_event_through_id"],
            series["instrument_id"],
            build["input_start_at"],
            build["end_at"],
            build["as_of"],
            series["source_revision_through_id"],
            series["source_revision_through_id"],
            series["instrument_id"],
            build["input_start_at"],
            build["end_at"],
            build["as_of"],
            series["candle_rollup_through_id"],
            series["candle_rollup_through_id"],
        ),
        source_kind_sql="projection.source_kind",
    )


def _stream_publication_hashes(
    connection: Any,
    build_id: int,
    specification: DatasetCanonicalSpecification,
    series_rows: Sequence[Row],
    heartbeat: _DatasetBuildLeaseHeartbeat,
) -> tuple[DatasetHashes, dict[int, tuple[int, str]]]:
    selection_hash = canonical_dataset_hashes(specification, ()).selection_hash
    manifest = CanonicalJsonArrayDigest()
    by_series = {int(row["id"]): CanonicalJsonArrayDigest() for row in series_rows}
    series_by_id = {int(row["id"]): row for row in series_rows}
    with connection.cursor(name=f"dataset_members_{build_id}") as cursor:
        cursor.execute(
            """
            SELECT * FROM dataset_publication_members
            ORDER BY data_kind COLLATE "C", exchange COLLATE "C", market_code COLLATE "C",
              unit COLLATE "C", COALESCE(definition_hash, 'None') COLLATE "C",
              calculation_version COLLATE "C", occurred_at, content_hash COLLATE "C"
            """
        )
        while rows := cursor.fetchmany(MAX_PUBLICATION_CHUNK_SIZE):
            heartbeat.renew()
            for row in rows:
                member = _canonical_member(series_by_id[int(row["build_series_id"])], row)
                manifest.add_canonical_member(member)
                by_series[int(row["build_series_id"])].add_canonical_member(member)

    market_status = CanonicalJsonArrayDigest()
    with connection.cursor(name=f"dataset_status_{build_id}") as cursor:
        cursor.execute(
            """
            SELECT * FROM dataset_build_market_status_snapshots
            WHERE dataset_build_id=%s
            ORDER BY exchange COLLATE "C", market_code COLLATE "C", valid_from, observed_at
            """,
            (build_id,),
        )
        while rows := cursor.fetchmany(MAX_PUBLICATION_CHUNK_SIZE):
            heartbeat.renew()
            for row in rows:
                payload = canonical_market_status_payload(_canonical_status(row))
                market_status.add(
                    payload,
                    (
                        str(payload["exchange"]),
                        str(payload["marketCode"]),
                        str(payload["validFrom"]),
                        str(payload["observedAt"]),
                    ),
                )

    coverage = CanonicalJsonArrayDigest()
    with connection.cursor(name=f"dataset_coverage_{build_id}") as cursor:
        cursor.execute(
            """
            SELECT * FROM dataset_build_coverage_snapshots
            WHERE dataset_build_id=%s
            ORDER BY data_kind COLLATE "C", exchange COLLATE "C", market_code COLLATE "C",
              unit COLLATE "C", COALESCE(definition_set_hash, 'None') COLLATE "C",
              calculation_version COLLATE "C", range_start_at
            """,
            (build_id,),
        )
        while rows := cursor.fetchmany(MAX_PUBLICATION_CHUNK_SIZE):
            heartbeat.renew()
            for row in rows:
                payload = canonical_coverage_payload(_canonical_coverage(row))
                coverage.add(
                    payload,
                    (
                        str(payload["dataKind"]),
                        str(payload["exchange"]),
                        str(payload["marketCode"]),
                        str(payload["unit"]),
                        str(payload["definitionSetHash"]),
                        str(payload["calculationVersion"]),
                        str(payload["rangeStartAt"]),
                    ),
                )

    manifest_hash = manifest.hexdigest()
    market_status_hash = market_status.hexdigest()
    coverage_hash = coverage.hexdigest()
    hashes = DatasetHashes(
        selection_hash=selection_hash,
        manifest_hash=manifest_hash,
        market_status_hash=market_status_hash,
        coverage_hash=coverage_hash,
        content_hash=canonical_payload_hash(
            {
                "selectionHash": selection_hash,
                "manifestHash": manifest_hash,
                "marketStatusHash": market_status_hash,
                "coverageHash": coverage_hash,
            }
        ),
    )
    series_digests = {
        series_id: (digest.count, digest.hexdigest())
        for series_id, digest in by_series.items()
    }
    return hashes, series_digests


def _insert_version_contents(
    connection: Any,
    version: Row,
    series_rows: Sequence[Row],
    series_digests: Mapping[int, tuple[int, str]],
    build_id: int,
) -> None:
    for series in series_rows:
        member_count, members_hash = series_digests[int(series["id"])]
        connection.execute(
            """
            INSERT INTO dataset_version_series (
              dataset_version_id, source_build_series_id, market_id, instrument_id,
              data_kind, unit, definition_set_hash, calculation_version,
              source_revision_through_id, candle_rollup_through_id,
              quality_event_through_id, indicator_materialization_through_id,
              market_statistic_through_id, microstructure_materialization_through_id,
              market_status_history_through_id, orderbook_snapshot_through_id,
              trade_event_through_id, source_receipt_through_id,
              connection_quality_through_id, member_count, members_hash
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
            """,
            (
                version["id"],
                series["id"],
                series["market_id"],
                series["instrument_id"],
                series["data_kind"],
                series["unit"],
                series["definition_set_hash"],
                series["calculation_version"],
                series["source_revision_through_id"],
                series["candle_rollup_through_id"],
                series["quality_event_through_id"],
                series["indicator_materialization_through_id"],
                series["market_statistic_through_id"],
                series["microstructure_materialization_through_id"],
                series["market_status_history_through_id"],
                series["orderbook_snapshot_through_id"],
                series["trade_event_through_id"],
                series["source_receipt_through_id"],
                series["connection_quality_through_id"],
                member_count,
                members_hash,
            ),
        )
    connection.execute(
        """
        INSERT INTO dataset_version_candles (
          dataset_version_id, dataset_version_series_id, instrument_id, unit,
          occurred_at, source_candle_revision_id, candle_rollup_id,
          quality, content_hash, knowledge_at, source_as_of
        )
        SELECT %s, version_series.id, member.instrument_id, member.unit,
          member.occurred_at,
          CASE WHEN member.source_kind='source' THEN member.source_ref_id END,
          CASE WHEN member.source_kind='rollup' THEN member.source_ref_id END,
          member.quality, member.content_hash, member.knowledge_at, member.source_as_of
        FROM dataset_publication_members member
        JOIN dataset_version_series version_series
          ON version_series.dataset_version_id=%s
         AND version_series.source_build_series_id=member.build_series_id
        WHERE member.data_kind='candle'
        """,
        (version["id"], version["id"]),
    )
    for kind, table, id_column in (
        ("indicator", "dataset_version_indicators", "indicator_materialization_id"),
        ("market_statistic", "dataset_version_market_statistics", "market_statistic_id"),
        (
            "microstructure",
            "dataset_version_microstructures",
            "microstructure_materialization_id",
        ),
    ):
        connection.execute(
            f"""
            INSERT INTO {table} (
              dataset_version_id, dataset_version_series_id, {id_column}, instrument_id,
              unit, occurred_at, quality, content_hash, knowledge_at, source_as_of
            )
            SELECT %s, version_series.id, member.source_ref_id, member.instrument_id,
              member.unit, member.occurred_at, member.quality, member.content_hash,
              member.knowledge_at, member.source_as_of
            FROM dataset_publication_members member
            JOIN dataset_version_series version_series
              ON version_series.dataset_version_id=%s
             AND version_series.source_build_series_id=member.build_series_id
            WHERE member.data_kind=%s
            """,
            (version["id"], version["id"], kind),
        )
    connection.execute(
        """
        INSERT INTO dataset_version_market_status_snapshots
        SELECT %s, id, source_market_status_history_id, market_id, exchange,
          market_code, trading_status, market_warning, market_event,
          source_payload_checksum, valid_from, valid_to, observed_at, snapshot_hash
        FROM dataset_build_market_status_snapshots WHERE dataset_build_id=%s
        """,
        (version["id"], build_id),
    )
    connection.execute(
        """
        INSERT INTO dataset_version_coverage_snapshots
        SELECT %s, coverage.id, version_series.id,
          coverage.source_data_quality_event_id, coverage.exchange, coverage.market_code,
          coverage.data_kind, coverage.unit, coverage.definition_set_hash,
          coverage.calculation_version, coverage.range_start_at, coverage.range_end_at,
          coverage.knowledge_at, coverage.status, coverage.observed_count,
          coverage.expected_count, coverage.evidence_hash
        FROM dataset_build_coverage_snapshots coverage
        JOIN dataset_version_series version_series
          ON version_series.dataset_version_id=%s
         AND version_series.source_build_series_id=coverage.dataset_build_series_id
        WHERE coverage.dataset_build_id=%s
        """,
        (version["id"], version["id"], build_id),
    )


def _read_series_rows(
    connection: Any,
    series: Row,
    start_at: datetime,
    end_at: datetime,
    after: datetime | None,
    limit: int,
) -> list[Row]:
    kind = str(series["data_kind"])
    table, join, values = {
        "candle": (
            "dataset_version_candles member",
            "LEFT JOIN source_candle_revisions source ON source.id=member.source_candle_revision_id "
            "LEFT JOIN candle_rollups rollup ON rollup.id=member.candle_rollup_id",
            "jsonb_build_object('open',COALESCE(source.open_price,rollup.open_price)::text,"
            "'high',COALESCE(source.high_price,rollup.high_price)::text,"
            "'low',COALESCE(source.low_price,rollup.low_price)::text,"
            "'close',COALESCE(source.close_price,rollup.close_price)::text,"
            "'volume',COALESCE(source.trade_volume,rollup.trade_volume)::text,"
            "'amount',COALESCE(source.trade_amount,rollup.trade_amount)::text)",
        ),
        "indicator": (
            "dataset_version_indicators member",
            "LEFT JOIN LATERAL (SELECT jsonb_object_agg("
            "definition.indicator_key || '.' || value.value_name, value.value::text) AS values "
            "FROM indicator_values value JOIN indicator_definition_versions definition "
            "ON definition.id=value.definition_version_id "
            "WHERE value.materialization_id=member.indicator_materialization_id) payload ON TRUE",
            "COALESCE(payload.values,'{}'::jsonb)",
        ),
        "market_statistic": (
            "dataset_version_market_statistics member",
            "JOIN market_statistics statistic ON statistic.id=member.market_statistic_id",
            "jsonb_strip_nulls(jsonb_build_object("
            "'closeReturn1',statistic.close_return_1::text,"
            "'realizedVolatility20',statistic.realized_volatility_20::text,"
            "'tradeVolume',statistic.trade_volume::text,"
            "'tradeAmount',statistic.trade_amount::text,"
            "'volatilitySampleCount',statistic.volatility_sample_count))",
        ),
        "microstructure": (
            "dataset_version_microstructures member",
            "JOIN microstructure_statistics statistic "
            "ON statistic.materialization_id=member.microstructure_materialization_id",
            "jsonb_strip_nulls(jsonb_build_object("
            "'spread',statistic.spread::text,'spreadBps',statistic.spread_bps::text,"
            "'bidDepth10',statistic.bid_depth_10::text,"
            "'askDepth10',statistic.ask_depth_10::text,"
            "'orderbookImbalance10',statistic.orderbook_imbalance_10::text,"
            "'tradeCount',statistic.trade_count,"
            "'executionStrength',statistic.execution_strength::text,"
            "'orderbookStatus',statistic.orderbook_status,"
            "'tradeStatus',statistic.trade_status))",
        ),
    }[kind]
    return connection.execute(
        f"""
        SELECT member.occurred_at, member.knowledge_at, member.quality,
               member.content_hash, {values} AS values
        FROM {table} {join}
        WHERE member.dataset_version_series_id=%s
          AND member.occurred_at >= %s AND member.occurred_at < %s
          AND (%s::timestamptz IS NULL OR member.occurred_at > %s)
        ORDER BY member.occurred_at LIMIT %s
        """,
        (series["id"], start_at, end_at, after, after, limit),
    ).fetchall()


def _canonical_member(series: Row, member: Row) -> DatasetCanonicalMember:
    return DatasetCanonicalMember(
        data_kind=cast(Any, series["data_kind"]),
        exchange=str(series["exchange"]),
        market_code=str(series["market_code"]),
        unit=str(series["unit"]),
        occurred_at=member["occurred_at"],
        knowledge_at=member["knowledge_at"],
        source_as_of=member["source_as_of"],
        content_hash=str(member["content_hash"]),
        quality=cast(Any, member["quality"]),
        calculation_version=str(member["calculation_version"]),
        definition_hash=member["definition_hash"],
        source_ref_id=int(member["source_ref_id"]),
    )


def _canonical_status(row: Row) -> DatasetMarketStatusSnapshot:
    return DatasetMarketStatusSnapshot(
        exchange=str(row["exchange"]),
        market_code=str(row["market_code"]),
        trading_status=str(row["trading_status"]),
        market_warning=str(row["market_warning"]),
        event_hash=canonical_payload_hash(cast(Any, row["market_event"])),
        source_payload_checksum=str(row["source_payload_checksum"]),
        valid_from=row["valid_from"],
        valid_to=row["valid_to"],
        observed_at=row["observed_at"],
        source_ref_id=int(row["id"]),
    )


def _canonical_coverage(row: Row) -> DatasetCoverageSegment:
    return DatasetCoverageSegment(
        data_kind=cast(Any, row["data_kind"]),
        exchange=str(row["exchange"]),
        market_code=str(row["market_code"]),
        unit=str(row["unit"]),
        definition_set_hash=row["definition_set_hash"],
        calculation_version=str(row["calculation_version"]),
        range_start_at=row["range_start_at"],
        range_end_at=row["range_end_at"],
        knowledge_at=row["knowledge_at"],
        status=cast(Any, row["status"]),
        observed_count=int(row["observed_count"]),
        expected_count=int(row["expected_count"]),
        evidence_hash=str(row["evidence_hash"]),
        source_ref_id=int(row["id"]),
    )


def _coverage_blocks_fail_publication(row: Mapping[str, object]) -> bool:
    status = str(row["status"])
    if status in {"missing", "unavailable", "unverified"}:
        return True
    return status == "available" and int(cast(int, row["observed_count"])) < int(
        cast(int, row["expected_count"])
    )


def _mark_build_failed(
    repository: object,
    build_id: int,
    worker_id: str,
    generation: int,
    error_code: str,
    message: str,
) -> None:
    with _connector(repository)() as connection:
        connection.execute(
            """
            UPDATE dataset_builds SET status='failed', lease_owner=NULL,
              lease_expires_at=NULL, last_error_code=%s,
              last_error_message=%s,
              finished_at=clock_timestamp()
            WHERE id=%s AND status='running' AND lease_owner=%s
              AND lease_generation=%s AND lease_expires_at > clock_timestamp()
            """,
            (error_code, message, build_id, worker_id, generation),
        )


def _schedule_build_retry(
    repository: object,
    build_id: int,
    worker_id: str,
    generation: int,
    error: Exception,
) -> None:
    with _connector(repository)() as connection:
        connection.execute(
            """
            UPDATE dataset_builds SET
              status=CASE WHEN attempt_count >= max_attempts
                          THEN 'dead_letter' ELSE 'retry_wait' END,
              lease_owner=NULL, lease_expires_at=NULL,
              next_retry_at=CASE WHEN attempt_count >= max_attempts THEN NULL
                ELSE clock_timestamp() + make_interval(secs => LEAST(300, power(2, attempt_count)::integer)) END,
              last_error_code='unexpected_publication_error',
              last_error_message=%s,
              dead_letter_reason=CASE WHEN attempt_count >= max_attempts
                THEN 'unexpected_publication_error' ELSE NULL END,
              finished_at=CASE WHEN attempt_count >= max_attempts
                THEN clock_timestamp() ELSE NULL END
            WHERE id=%s AND status='running' AND lease_owner=%s
              AND lease_generation=%s AND lease_expires_at > clock_timestamp()
            """,
            (str(error), build_id, worker_id, generation),
        )


def _build_response(row: Row) -> Row:
    return {
        "buildId": int(row["id"]),
        "requestId": row["request_id"],
        "idempotencyKey": row["idempotency_key"],
        "actorId": row["actor_id"],
        "requestedAt": row["requested_at"],
        "frozenAt": row["frozen_at"],
        "status": row["status"],
        "datasetVersionId": int(row["dataset_version_id"])
        if row["dataset_version_id"] is not None
        else None,
        "errorCode": row["last_error_code"],
        "errorMessage": row["last_error_message"],
        "attemptCount": int(row["attempt_count"]),
        "maxAttempts": int(row["max_attempts"]),
        "nextRetryAt": row["next_retry_at"],
        "deadLetterReason": row["dead_letter_reason"],
    }


def _series_point(row: Row) -> Mapping[str, object]:
    return {
        "occurredAt": row["occurred_at"],
        "knowledgeAt": row["knowledge_at"],
        "quality": row["quality"],
        "contentHash": row["content_hash"],
        "values": row["values"],
    }


def _expected_bucket_count(start_at: datetime, end_at: datetime, unit: str) -> int:
    minutes = {
        "1m": 1,
        "3m": 3,
        "5m": 5,
        "10m": 10,
        "15m": 15,
        "30m": 30,
        "1h": 60,
        "4h": 240,
        "1d": 1440,
    }
    if unit == "1M":
        return max(0, _month_grid_ceiling(end_at) - _month_grid_ceiling(start_at))
    if unit == "1w":
        anchor = datetime(1970, 1, 5, tzinfo=UTC)
        step = timedelta(days=7)
    else:
        anchor = datetime(1970, 1, 1, tzinfo=UTC)
        step = timedelta(minutes=minutes[unit])
    return max(0, _fixed_grid_ceiling(end_at, anchor, step) - _fixed_grid_ceiling(start_at, anchor, step))


def _fixed_grid_ceiling(value: datetime, anchor: datetime, step: timedelta) -> int:
    quotient, remainder = divmod(value - anchor, step)
    return quotient if remainder == timedelta(0) else quotient + 1


def _month_grid_ceiling(value: datetime) -> int:
    month_index = value.year * 12 + value.month - 1
    month_start = datetime(value.year, value.month, 1, tzinfo=UTC)
    return month_index if value == month_start else month_index + 1


def _normalize_source_quality(value: object) -> str:
    return {"observed": "available", "failed": "missing"}.get(str(value), str(value))


def _store_kind(value: str) -> str:
    return "market_statistic" if value == "market_statistics" else value


def _api_kind(value: str) -> str:
    return value


def _connector(repository: object) -> Any:
    connector = getattr(repository, "_connect", None)
    if not callable(connector):
        raise RuntimeError("PostgreSQL dataset version 저장소 연결이 필요하다.")
    return connector


def _jsonable(value: object) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str):
        return [_jsonable(item) for item in value]
    return value


def _encode_series_cursor(
    *,
    dataset_version_id: int,
    series_id: int,
    from_at: datetime,
    to_at: datetime,
    last_occurred_at: datetime,
) -> str:
    payload = {
        "datasetVersionId": dataset_version_id,
        "seriesId": series_id,
        "from": from_at.isoformat(),
        "to": to_at.isoformat(),
        "lastOccurredAt": last_occurred_at.isoformat(),
    }
    envelope = {"payload": payload, "hash": canonical_payload_hash(cast(Any, payload))}
    encoded = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(encoded).decode().rstrip("=")


def _decode_series_cursor(
    value: str | None,
    *,
    dataset_version_id: int,
    series_id: int,
    from_at: datetime,
    to_at: datetime,
) -> datetime | None:
    if value is None:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        envelope = json.loads(base64.urlsafe_b64decode(padded).decode())
        payload = cast(Mapping[str, object], envelope["payload"])
        if envelope["hash"] != canonical_payload_hash(cast(Any, payload)):
            raise DatasetCursorMismatchError("series cursor 무결성 검증에 실패했다.")
        expected = (dataset_version_id, series_id, from_at.isoformat(), to_at.isoformat())
        actual = (
            payload["datasetVersionId"],
            payload["seriesId"],
            payload["from"],
            payload["to"],
        )
        if actual != expected:
            raise DatasetCursorMismatchError("series cursor 요청 context가 다르다.")
        return datetime.fromisoformat(str(payload["lastOccurredAt"]))
    except DatasetCursorMismatchError:
        raise
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DatasetCursorMismatchError("유효하지 않은 series cursor다.") from exc


def _encode_list_cursor(ceiling: int, last_id: int) -> str:
    payload = {"ceiling": ceiling, "lastId": last_id}
    envelope = {"payload": payload, "hash": canonical_payload_hash(payload)}
    encoded = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(encoded).decode().rstrip("=")


def _decode_list_cursor(value: str | None) -> Mapping[str, object] | None:
    if value is None:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        envelope = json.loads(base64.urlsafe_b64decode(padded).decode())
        if not isinstance(envelope, dict):
            raise DatasetCursorMismatchError("유효하지 않은 dataset version cursor 구조다.")
        raw_payload = envelope["payload"]
        if not isinstance(raw_payload, dict):
            raise DatasetCursorMismatchError("유효하지 않은 dataset version cursor 구조다.")
        payload = cast(Mapping[str, object], raw_payload)
        if envelope["hash"] != canonical_payload_hash(cast(Any, payload)):
            raise DatasetCursorMismatchError("dataset version cursor 무결성 검증에 실패했다.")
        if set(payload) != {"ceiling", "lastId"}:
            raise DatasetCursorMismatchError("유효하지 않은 dataset version cursor 구조다.")
        ceiling = payload["ceiling"]
        last_id = payload["lastId"]
        if (
            type(ceiling) is not int
            or type(last_id) is not int
            or ceiling < 0
            or last_id < 1
            or last_id > ceiling
        ):
            raise DatasetCursorMismatchError("유효하지 않은 dataset version cursor 범위다.")
        return payload
    except DatasetCursorMismatchError:
        raise
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DatasetCursorMismatchError("유효하지 않은 dataset version cursor다.") from exc
