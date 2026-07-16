from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from goodmoneying_shared.data_foundation import (
    COVERAGE_ADVISORY_LOCK_NAMESPACE,
    CoverageState,
)


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
        connection.execute(
            """
            INSERT INTO data_quality_events (
              target_spec_id, event_type, previous_status, new_status,
              range_start_at, range_end_at, fingerprint, evidence,
              fetch_manifest_id, detected_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (
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
