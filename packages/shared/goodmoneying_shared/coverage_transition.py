from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from goodmoneying_shared.aggregation import CALCULATION_VERSION, MATERIALIZED_AGGREGATION_UNITS
from goodmoneying_shared.data_foundation import (
    COVERAGE_ADVISORY_LOCK_NAMESPACE,
    ROLLUP_FRONTIER_ADVISORY_LOCK_NAMESPACE,
    CoverageState,
)
from goodmoneying_shared.incremental_aggregation import affected_rollup_ranges_for_interval


def replace_coverage_with_classification(
    connection: psycopg.Connection[Any],
    *,
    target_spec_id: int,
    range_start_at: datetime,
    range_end_at: datetime,
    status: CoverageState,
    reason_code: str,
    manifest_id: int | None,
    evidence: dict[str, Any],
) -> None:
    """하나의 반개방 구간을 분류 결과로 교체하고 전이 이벤트를 기록한다."""

    if range_start_at >= range_end_at:
        return
    connection.execute(
        "SELECT pg_advisory_xact_lock(%s, %s)",
        (COVERAGE_ADVISORY_LOCK_NAMESPACE, target_spec_id),
    )
    market = connection.execute(
        "SELECT market_id FROM collection_target_specs WHERE id = %s",
        (target_spec_id,),
    ).fetchone()
    if market is None:
        return
    connection.execute(
        "SELECT pg_advisory_xact_lock(%s, %s)",
        (ROLLUP_FRONTIER_ADVISORY_LOCK_NAMESPACE, market["market_id"]),
    )
    overlaps = connection.execute(
        """
        SELECT *
        FROM coverage_intervals
        WHERE target_spec_id = %s
          AND tstzrange(range_start_at, range_end_at, '[)')
              && tstzrange(%s, %s, '[)')
        ORDER BY range_start_at
        FOR UPDATE
        """,
        (target_spec_id, range_start_at, range_end_at),
    ).fetchall()
    assessed_at = datetime.now(UTC)
    transitions: list[tuple[str | None, datetime, datetime]] = []
    covered_ranges: list[tuple[datetime, datetime]] = []
    for overlap in overlaps:
        connection.execute("DELETE FROM coverage_intervals WHERE id = %s", (overlap["id"],))
        transition_start = max(overlap["range_start_at"], range_start_at)
        transition_end = min(overlap["range_end_at"], range_end_at)
        if transition_start < transition_end:
            covered_ranges.append((transition_start, transition_end))
        if overlap["status"] != status and transition_start < transition_end:
            transitions.append((str(overlap["status"]), transition_start, transition_end))
        for preserved_start, preserved_end in (
            (overlap["range_start_at"], min(overlap["range_end_at"], range_start_at)),
            (max(overlap["range_start_at"], range_end_at), overlap["range_end_at"]),
        ):
            if preserved_start >= preserved_end:
                continue
            connection.execute(
                """
                INSERT INTO coverage_intervals (
                  target_spec_id, range_start_at, range_end_at, status,
                  evidence, fetch_manifest_id, assessed_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    target_spec_id,
                    preserved_start,
                    preserved_end,
                    overlap["status"],
                    Jsonb(overlap["evidence"]),
                    overlap["fetch_manifest_id"],
                    overlap["assessed_at"],
                ),
            )
    uncovered_start = range_start_at
    for covered_start, covered_end in covered_ranges:
        if uncovered_start < covered_start:
            transitions.append((None, uncovered_start, covered_start))
        uncovered_start = max(uncovered_start, covered_end)
    if uncovered_start < range_end_at:
        transitions.append((None, uncovered_start, range_end_at))

    normalized_evidence = _jsonable(evidence)
    connection.execute(
        """
        INSERT INTO coverage_intervals (
          target_spec_id, range_start_at, range_end_at, status,
          evidence, fetch_manifest_id, assessed_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            target_spec_id,
            range_start_at,
            range_end_at,
            status,
            Jsonb(normalized_evidence),
            manifest_id,
            assessed_at,
        ),
    )
    created_events: list[tuple[int, datetime, datetime]] = []
    for previous_status, event_start, event_end in transitions:
        fingerprint = _checksum(
            {
                "targetSpecId": target_spec_id,
                "previousStatus": previous_status,
                "newStatus": status,
                "rangeStartAt": event_start,
                "rangeEndAt": event_end,
                "reasonCode": reason_code,
                "fetchManifestId": manifest_id,
            }
        )
        event_evidence = {
            **evidence,
            "reasonCode": reason_code,
            "fetchManifestId": manifest_id,
        }
        quality_event_row = connection.execute(
            "SELECT nextval('data_quality_events_id_seq') AS id"
        ).fetchone()
        assert quality_event_row is not None
        quality_event_id = int(quality_event_row["id"])
        created = connection.execute(
            """
            INSERT INTO data_quality_events (
              id, target_spec_id, event_type, previous_status, new_status,
              range_start_at, range_end_at, fingerprint, evidence,
              fetch_manifest_id, detected_at
            )
            OVERRIDING SYSTEM VALUE
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (
                quality_event_id,
                target_spec_id,
                reason_code,
                previous_status,
                status,
                event_start,
                event_end,
                fingerprint,
                Jsonb(_jsonable(event_evidence)),
                manifest_id,
                assessed_at,
            ),
        )
        if created.rowcount == 1:
            created_events.append((quality_event_id, event_start, event_end))
    for quality_event_id, event_start, event_end in created_events:
        _enqueue_quality_transition_recomputation(
            connection,
            target_spec_id=target_spec_id,
            quality_event_id=quality_event_id,
            event_start_at=event_start,
            event_end_at=event_end,
            knowledge_at=assessed_at,
        )


def _enqueue_quality_transition_recomputation(
    connection: psycopg.Connection[Any],
    *,
    target_spec_id: int,
    quality_event_id: int,
    event_start_at: datetime,
    event_end_at: datetime,
    knowledge_at: datetime,
) -> None:
    context = connection.execute(
        """
        SELECT specification.market_id, specification.data_type,
               specification.candle_unit, market.legacy_instrument_id AS instrument_id
        FROM collection_target_specs specification
        JOIN markets market ON market.id = specification.market_id
        WHERE specification.id = %s
        """,
        (target_spec_id,),
    ).fetchone()
    if (
        context is None
        or context["data_type"] != "source_candle"
        or context["candle_unit"] not in {"1m", "1d"}
        or context["instrument_id"] is None
    ):
        return
    instrument_id = int(context["instrument_id"])
    bounds = connection.execute(
        """
        SELECT MIN(candle_start_at) AS first_at, MAX(candle_start_at) AS last_at,
               MAX(id) AS source_revision_through_id
        FROM source_candle_revisions
        WHERE instrument_id = %s
        """,
        (instrument_id,),
    ).fetchone()
    if (
        bounds is None
        or bounds["first_at"] is None
        or bounds["last_at"] is None
        or bounds["source_revision_through_id"] is None
    ):
        return
    affected_start = max(event_start_at, bounds["first_at"] - timedelta(days=32))
    affected_end = min(event_end_at, bounds["last_at"] + timedelta(days=32))
    affected_units = (
        ("1d", "1w", "1M")
        if context["candle_unit"] == "1d"
        else MATERIALIZED_AGGREGATION_UNITS
    )
    for affected in affected_rollup_ranges_for_interval(
        affected_start, affected_end, units=affected_units
    ):
        contains_source = connection.execute(
            """
            SELECT 1 FROM source_candle_revisions
            WHERE instrument_id = %s
              AND candle_start_at >= %s AND candle_start_at < %s
            LIMIT 1
            """,
            (instrument_id, affected.start_at, affected.end_at),
        ).fetchone()
        if contains_source is None:
            continue
        coverage_rows = connection.execute(
            """
            SELECT GREATEST(coverage.range_start_at, %s) AS range_start_at,
                   LEAST(coverage.range_end_at, %s) AS range_end_at,
                   coverage.status
            FROM coverage_intervals coverage
            JOIN collection_target_specs specification
              ON specification.id = coverage.target_spec_id
            WHERE specification.market_id = %s
              AND specification.data_type = 'source_candle'
              AND specification.candle_unit IN ('1m', '1d')
              AND tstzrange(coverage.range_start_at, coverage.range_end_at, '[)')
                  && tstzrange(%s, %s, '[)')
            ORDER BY range_start_at, range_end_at,
                     specification.candle_unit, coverage.status,
                     specification.id, coverage.id
            """,
            (
                affected.start_at,
                affected.end_at,
                context["market_id"],
                affected.start_at,
                affected.end_at,
            ),
        ).fetchall()
        coverage_payload = [
            {
                "startAt": row["range_start_at"].astimezone(UTC).isoformat(),
                "endAt": row["range_end_at"].astimezone(UTC).isoformat(),
                "status": str(row["status"]),
            }
            for row in coverage_rows
        ]
        coverage_json = json.dumps(
            coverage_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        coverage_hash = hashlib.sha256(coverage_json.encode()).hexdigest()
        fingerprint = _checksum(
            {
                "marketId": int(context["market_id"]),
                "instrumentId": instrument_id,
                "unit": affected.unit,
                "startAt": affected.start_at,
                "endAt": affected.end_at,
                "calculationVersion": CALCULATION_VERSION,
                "qualityEventId": quality_event_id,
                "coverageSnapshotHash": coverage_hash,
            }
        )
        invalidation = connection.execute(
            """
            INSERT INTO candle_rollup_invalidations (
              idempotency_key, market_id, instrument_id, candle_unit,
              calculation_version, range_start_at, range_end_at,
              output_bucket_count, source_revision_ids,
              source_revision_through_id, quality_event_through_id,
              coverage_snapshot, coverage_snapshot_hash, knowledge_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (idempotency_key) DO NOTHING RETURNING id
            """,
            (
                fingerprint,
                context["market_id"],
                instrument_id,
                affected.unit,
                CALCULATION_VERSION,
                affected.start_at,
                affected.end_at,
                affected.output_bucket_count,
                [],
                bounds["source_revision_through_id"],
                quality_event_id,
                Jsonb(coverage_payload),
                coverage_hash,
                knowledge_at,
            ),
        ).fetchone()
        if invalidation is not None:
            connection.execute(
                """
                INSERT INTO candle_rollup_recompute_jobs (
                  invalidation_id, idempotency_key, status
                ) VALUES (%s, %s, 'pending')
                """,
                (invalidation["id"], fingerprint),
            )


def _jsonable(value: object) -> Any:
    return json.loads(
        json.dumps(value, default=str, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def _checksum(value: object) -> str:
    canonical = json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
