from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from math import ceil
from typing import Any, cast

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from goodmoneying_shared.coverage_transition import replace_coverage_with_classification
from goodmoneying_shared.data_foundation import (
    INSTRUMENT_ADVISORY_LOCK_NAMESPACE,
    CoverageEvidence,
    CoverageState,
    DataFoundationOverview,
    LeasedBackfillJob,
    MarketCatalogItem,
    MarketCollectionPolicySettings,
    MarketCollectionStatus,
    MarketStatusRevision,
    MarketSyncResult,
    build_default_krw_targets,
    classify_coverage,
)

Row = dict[str, Any]
DEFAULT_POLICY_NAME = "default-krw-2024"
POLICY_DISABLED_REASON = "policy:data-type-disabled"
CATALOG_MISSING_STATE_REASON = "catalog_missing"
MARKET_INACTIVE_STATE_REASON = "market_inactive"
OPERATOR_PAUSED_STATE_REASON = "operator_paused"
OPERATOR_EXCLUDED_STATE_REASON = "operator_excluded"
POLICY_DISABLED_STATE_REASON = "policy_data_type_disabled"
COVERAGE_OPEN_END_AT = datetime(9999, 1, 1, tzinfo=UTC)


def _require_utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{name}은 UTC timezone-aware datetime이어야 한다.")


def _status_fingerprint(item: MarketCatalogItem) -> tuple[str, dict[str, object], str]:
    trading_status = "active" if item.tradable else "inactive"
    event: dict[str, object] = {"trading_suspended": not item.tradable}
    canonical = json.dumps(
        {
            "trading_status": trading_status,
            "market_warning": item.market_warning,
            "market_event": event,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return trading_status, event, hashlib.sha256(canonical.encode()).hexdigest()


class PostgresDataFoundationRepository:
    """P1 시장·정책·내구성 작업·커버리지 계약 저장소."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def _connect(self) -> psycopg.Connection[Any]:
        return psycopg.connect(
            self._database_url,
            row_factory=dict_row,
            options="-c timezone=UTC",
        )

    def sync_market_catalog(
        self,
        catalog: list[MarketCatalogItem],
        *,
        observed_at: datetime,
    ) -> MarketSyncResult:
        _require_utc(observed_at, "observed_at")
        market_codes = [item.market_code for item in catalog]
        if len(market_codes) != len(set(market_codes)):
            raise ValueError("시장 카탈로그에 중복 market_code가 있다.")

        new_history_count = 0
        default_target_count = 0
        created_backfill_job_count = 0
        with self._connect() as connection:
            policy_id = self._ensure_default_policy(connection)
            observed_market_ids: set[int] = set()
            for item in catalog:
                quote_currency, separator, base_asset = item.market_code.partition("-")
                if not separator or not quote_currency or not base_asset:
                    raise ValueError(f"잘못된 Upbit market_code다: {item.market_code}")
                instrument = connection.execute(
                    """
                    INSERT INTO instruments (
                      exchange, market_code, quote_currency, base_asset, display_name, status
                    )
                    VALUES ('UPBIT', %s, %s, %s, %s, %s)
                    ON CONFLICT (exchange, market_code) DO UPDATE SET
                      quote_currency = excluded.quote_currency,
                      base_asset = excluded.base_asset,
                      display_name = excluded.display_name,
                      status = excluded.status,
                      updated_at = now()
                    RETURNING id
                    """,
                    (
                        item.market_code,
                        quote_currency,
                        base_asset,
                        item.korean_name,
                        "active" if item.tradable else "inactive",
                    ),
                ).fetchone()
                assert instrument is not None
                instrument_id = int(instrument["id"])
                market = connection.execute(
                    """
                    INSERT INTO markets (
                      exchange, market_code, quote_currency, base_asset,
                      korean_name, english_name, legacy_instrument_id,
                      first_observed_at, last_observed_at
                    )
                    VALUES ('UPBIT', %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (exchange, market_code) DO UPDATE SET
                      quote_currency = excluded.quote_currency,
                      base_asset = excluded.base_asset,
                      korean_name = excluded.korean_name,
                      english_name = excluded.english_name,
                      legacy_instrument_id = COALESCE(
                        markets.legacy_instrument_id, excluded.legacy_instrument_id
                      ),
                      last_observed_at = GREATEST(
                        markets.last_observed_at, excluded.last_observed_at
                      ),
                      updated_at = now()
                    RETURNING id
                    """,
                    (
                        item.market_code,
                        quote_currency,
                        base_asset,
                        item.korean_name,
                        item.english_name,
                        instrument_id,
                        observed_at,
                        observed_at,
                    ),
                ).fetchone()
                assert market is not None
                market_id = int(market["id"])
                observed_market_ids.add(market_id)
                if self._record_status_revision(
                    connection,
                    market_id,
                    item,
                    observed_at=observed_at,
                ):
                    new_history_count += 1

                targets = build_default_krw_targets(item, observed_at=observed_at)
                if not targets:
                    continue
                legacy_target_id = self._ensure_legacy_collection_target(
                    connection,
                    instrument_id,
                    active=item.tradable,
                    observed_at=observed_at,
                )
                self._ensure_legacy_collection_plan(
                    connection,
                    instrument_id,
                    observed_at=observed_at,
                )
                market_collection_active = False
                for target in targets:
                    target_spec = connection.execute(
                        """
                        SELECT id, status, continuous, state_reason, exclusion_reason
                        FROM collection_target_specs
                        WHERE policy_id = %s AND market_id = %s AND data_type = %s
                          AND NOT auto_managed
                        ORDER BY id
                        LIMIT 1
                        """,
                        (policy_id, market_id, target.data_type),
                    ).fetchone()
                    if target_spec is not None:
                        preserve_pause = target_spec["state_reason"] in {
                            OPERATOR_PAUSED_STATE_REASON,
                            POLICY_DISABLED_STATE_REASON,
                        }
                        if target_spec["status"] == "excluded" or preserve_pause:
                            restored_status = str(target_spec["status"])
                            restored_state_reason = target_spec["state_reason"]
                        elif item.tradable:
                            restored_status = "active"
                            restored_state_reason = None
                        else:
                            restored_status = "paused"
                            restored_state_reason = MARKET_INACTIVE_STATE_REASON
                        if (
                            target_spec["status"] != restored_status
                            or target_spec["state_reason"] != restored_state_reason
                        ):
                            target_spec = connection.execute(
                                """
                                UPDATE collection_target_specs
                                SET status = %s, state_reason = %s, updated_at = %s
                                WHERE id = %s
                                RETURNING id, status, continuous, state_reason, exclusion_reason
                                """,
                                (
                                    restored_status,
                                    restored_state_reason,
                                    observed_at,
                                    target_spec["id"],
                                ),
                            ).fetchone()
                    if target_spec is None:
                        target_spec = connection.execute(
                            """
                            INSERT INTO collection_target_specs (
                              policy_id, market_id, legacy_target_id, data_type,
                              candle_unit, range_start_at, retention_days, priority,
                              continuous, auto_managed, status, state_reason
                            )
                            VALUES (
                              %s, %s, %s, %s, %s, %s, %s, %s, %s, true, %s, %s
                            )
                            ON CONFLICT (
                              policy_id, market_id, data_type, candle_unit
                            ) DO UPDATE SET
                              legacy_target_id = COALESCE(
                                collection_target_specs.legacy_target_id,
                                excluded.legacy_target_id
                              ),
                              range_start_at = LEAST(
                                collection_target_specs.range_start_at,
                                excluded.range_start_at
                              ),
                              retention_days = excluded.retention_days,
                              priority = excluded.priority,
                              continuous = excluded.continuous,
                              status = CASE
                                WHEN collection_target_specs.status = 'excluded'
                                  THEN 'excluded'
                                ELSE excluded.status
                              END,
                              state_reason = CASE
                                WHEN collection_target_specs.status = 'excluded'
                                  THEN collection_target_specs.state_reason
                                ELSE excluded.state_reason
                              END,
                              updated_at = now()
                            RETURNING id, status, continuous, state_reason
                            """,
                            (
                                policy_id,
                                market_id,
                                legacy_target_id,
                                target.data_type,
                                target.candle_unit,
                                target.start_at,
                                target.retention_days,
                                target.priority,
                                target.continuous,
                                "active" if item.tradable else "paused",
                                None if item.tradable else MARKET_INACTIVE_STATE_REASON,
                            ),
                        ).fetchone()
                    assert target_spec is not None
                    market_collection_active = (
                        market_collection_active or target_spec["status"] == "active"
                    )
                    target_spec_id = int(target_spec["id"])
                    default_target_count += 1
                    self._ensure_initial_coverage(
                        connection,
                        target_spec_id=target_spec_id,
                        data_type=target.data_type,
                        start_at=target.start_at,
                        observed_at=observed_at,
                    )
                    if (
                        target.data_type == "source_candle"
                        and target_spec["status"] == "active"
                        and self._ensure_backfill_job(
                            connection,
                            target_spec_id=target_spec_id,
                            instrument_id=instrument_id,
                            market_code=item.market_code,
                            start_at=target.start_at,
                            end_at=observed_at,
                            priority=target.priority,
                        )
                    ):
                        created_backfill_job_count += 1
                    desired_state = (
                        "subscribed"
                        if target_spec["status"] == "active"
                        and item.tradable
                        and bool(target_spec["continuous"])
                        else "unsubscribed"
                    )
                    connection.execute(
                        """
                        INSERT INTO collection_subscription_desires (
                          target_spec_id, desired_state
                        )
                        VALUES (%s, %s)
                        ON CONFLICT (target_spec_id) DO UPDATE SET
                          desired_state = excluded.desired_state,
                          generation = CASE
                            WHEN collection_subscription_desires.desired_state
                              IS DISTINCT FROM excluded.desired_state
                            THEN collection_subscription_desires.generation + 1
                            ELSE collection_subscription_desires.generation
                          END,
                          updated_at = now()
                        """,
                        (target_spec_id, desired_state),
                    )
                self._transition_market_availability_coverage(
                    connection,
                    market_id=market_id,
                    changed_at=observed_at,
                    unavailable_reason=(None if item.tradable else MARKET_INACTIVE_STATE_REASON),
                )
                connection.execute(
                    """
                    UPDATE collection_targets
                    SET status = %s,
                        activated_at = CASE
                          WHEN %s THEN COALESCE(activated_at, %s)
                          ELSE activated_at
                        END,
                        deactivated_at = CASE WHEN %s THEN NULL ELSE %s END,
                        updated_at = %s
                    WHERE id = %s
                    """,
                    (
                        "active" if market_collection_active else "inactive",
                        market_collection_active,
                        observed_at,
                        market_collection_active,
                        observed_at,
                        observed_at,
                        legacy_target_id,
                    ),
                )
                self._transition_market_backfill_work(
                    connection,
                    instrument_id=instrument_id,
                    state="active" if item.tradable else "paused",
                    changed_at=observed_at,
                )

            new_history_count += self._mark_missing_markets_inactive(
                connection,
                observed_market_ids=observed_market_ids,
                observed_at=observed_at,
            )

        return MarketSyncResult(
            market_count=len(catalog),
            new_history_count=new_history_count,
            default_target_count=default_target_count,
            created_backfill_job_count=created_backfill_job_count,
        )

    def _ensure_initial_coverage(
        self,
        connection: psycopg.Connection[Any],
        *,
        target_spec_id: int,
        data_type: str,
        start_at: datetime,
        observed_at: datetime,
    ) -> None:
        if start_at >= observed_at:
            return
        status: CoverageState = classify_coverage(
            CoverageEvidence(
                outside_source_retention=data_type != "source_candle",
            )
        )
        reason = (
            "자동 백필이 원천 행과 manifest 증거를 확정하기 전"
            if status == "unverified"
            else "실시간 구독 전에 지나간 원천 이벤트는 공식 API로 복원할 수 없음"
        )
        inserted = connection.execute(
            """
            INSERT INTO coverage_intervals (
              target_spec_id, range_start_at, range_end_at, status,
              evidence, assessed_at
            )
            SELECT %s, %s, %s, %s, %s, %s
            WHERE NOT EXISTS (
              SELECT 1 FROM coverage_intervals WHERE target_spec_id = %s
            )
            RETURNING id
            """,
            (
                target_spec_id,
                start_at,
                observed_at,
                status,
                Jsonb(
                    {
                        "classification": "policy_initialization",
                        "dataType": data_type,
                        "reason": reason,
                    }
                ),
                observed_at,
                target_spec_id,
            ),
        ).fetchone()
        if inserted is None:
            return
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "targetSpecId": target_spec_id,
                    "previousStatus": None,
                    "newStatus": status,
                    "rangeStartAt": start_at.isoformat(),
                    "rangeEndAt": observed_at.isoformat(),
                    "reasonCode": "policy_initialization",
                    "fetchManifestId": None,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        connection.execute(
            """
            INSERT INTO data_quality_events (
              target_spec_id, event_type, previous_status, new_status,
              range_start_at, range_end_at, fingerprint, evidence,
              fetch_manifest_id, detected_at
            )
            VALUES (%s, 'policy_initialization', NULL, %s, %s, %s, %s, %s, NULL, %s)
            ON CONFLICT DO NOTHING
            """,
            (
                target_spec_id,
                status,
                start_at,
                observed_at,
                fingerprint,
                Jsonb(
                    {
                        "classification": "policy_initialization",
                        "dataType": data_type,
                        "reason": reason,
                        "reasonCode": "policy_initialization",
                    }
                ),
                observed_at,
            ),
        )

    def _transition_market_availability_coverage(
        self,
        connection: psycopg.Connection[Any],
        *,
        market_id: int,
        changed_at: datetime,
        unavailable_reason: str | None,
    ) -> None:
        target_ids = [
            int(row["id"])
            for row in connection.execute(
                "SELECT id FROM collection_target_specs WHERE market_id = %s ORDER BY id",
                (market_id,),
            ).fetchall()
        ]
        if unavailable_reason is not None:
            unavailable_status = classify_coverage(
                CoverageEvidence(after_trading_end=True)
            )
            event_type = f"{unavailable_reason}_unavailable"
            for target_spec_id in target_ids:
                replace_coverage_with_classification(
                    connection,
                    target_spec_id=target_spec_id,
                    range_start_at=changed_at,
                    range_end_at=COVERAGE_OPEN_END_AT,
                    status=unavailable_status,
                    reason_code=event_type,
                    manifest_id=None,
                    evidence={
                        "classification": "market_catalog_availability",
                        "reasonCode": unavailable_reason,
                    },
                )
            return

        for target_spec_id in target_ids:
            market_unavailable = connection.execute(
                """
                SELECT range_start_at, range_end_at
                FROM coverage_intervals
                WHERE target_spec_id = %s
                  AND status = 'unavailable'
                  AND evidence ->> 'reasonCode' IN (%s, %s)
                  AND range_end_at > %s
                ORDER BY range_start_at
                """,
                (
                    target_spec_id,
                    CATALOG_MISSING_STATE_REASON,
                    MARKET_INACTIVE_STATE_REASON,
                    changed_at,
                ),
            ).fetchall()
            for interval in market_unavailable:
                resumed_status = classify_coverage(
                    CoverageEvidence(market_trading_resumed=True)
                )
                replace_coverage_with_classification(
                    connection,
                    target_spec_id=target_spec_id,
                    range_start_at=max(changed_at, cast(datetime, interval["range_start_at"])),
                    range_end_at=cast(datetime, interval["range_end_at"]),
                    status=resumed_status,
                    reason_code="market_trading_resumed",
                    manifest_id=None,
                    evidence={
                        "classification": "market_catalog_availability",
                        "reasonCode": "market_trading_resumed",
                    },
                )

    def _ensure_default_policy(self, connection: psycopg.Connection[Any]) -> int:
        row = connection.execute(
            """
            INSERT INTO collection_policies (
              exchange, quote_currency, name, default_start_at,
              retention_days, priority, auto_include_new_markets, status
            )
            VALUES (
              'UPBIT', 'KRW', %s, '2024-01-01T00:00:00Z',
              NULL, 100, true, 'active'
            )
            ON CONFLICT (exchange, quote_currency, name) DO UPDATE SET
              auto_include_new_markets = true,
              updated_at = now()
            RETURNING id
            """,
            (DEFAULT_POLICY_NAME,),
        ).fetchone()
        assert row is not None
        return int(row["id"])

    def _record_status_revision(
        self,
        connection: psycopg.Connection[Any],
        market_id: int,
        item: MarketCatalogItem,
        *,
        observed_at: datetime,
    ) -> bool:
        trading_status, event, checksum = _status_fingerprint(item)
        current = connection.execute(
            """
            SELECT *
            FROM market_status_history
            WHERE market_id = %s AND valid_to IS NULL
            ORDER BY valid_from DESC
            LIMIT 1
            FOR UPDATE
            """,
            (market_id,),
        ).fetchone()
        if current is not None and current["source_payload_checksum"] == checksum:
            return False
        if current is not None:
            connection.execute(
                """
                UPDATE market_status_history
                SET valid_to = %s
                WHERE id = %s
                """,
                (observed_at, current["id"]),
            )
        connection.execute(
            """
            INSERT INTO market_status_history (
              market_id, trading_status, market_warning, market_event,
              source_payload_checksum, valid_from, observed_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                market_id,
                trading_status,
                item.market_warning,
                Jsonb(event),
                checksum,
                observed_at,
                observed_at,
            ),
        )
        return True

    def _mark_missing_markets_inactive(
        self,
        connection: psycopg.Connection[Any],
        *,
        observed_market_ids: set[int],
        observed_at: datetime,
    ) -> int:
        if observed_market_ids:
            rows = connection.execute(
                """
                SELECT m.id, m.market_code, m.korean_name, m.english_name,
                       m.legacy_instrument_id
                FROM markets m
                JOIN market_status_history history
                  ON history.market_id = m.id AND history.valid_to IS NULL
                WHERE m.exchange = 'UPBIT'
                  AND history.trading_status = 'active'
                  AND NOT (m.id = ANY(%s))
                """,
                (list(observed_market_ids),),
            ).fetchall()
        else:
            rows = []
        count = 0
        for row in rows:
            item = MarketCatalogItem(
                market_code=str(row["market_code"]),
                korean_name=str(row["korean_name"]),
                english_name=str(row["english_name"]),
                market_warning="NONE",
                tradable=False,
            )
            if self._record_status_revision(
                connection,
                int(row["id"]),
                item,
                observed_at=observed_at,
            ):
                count += 1
            connection.execute(
                """
                UPDATE collection_target_specs
                SET status = CASE WHEN status = 'excluded' THEN status ELSE 'paused' END,
                    state_reason = CASE
                      WHEN status = 'excluded' THEN state_reason
                      WHEN state_reason IN (%s, %s) THEN state_reason
                      ELSE %s
                    END,
                    updated_at = now()
                WHERE market_id = %s
                """,
                (
                    OPERATOR_PAUSED_STATE_REASON,
                    POLICY_DISABLED_STATE_REASON,
                    CATALOG_MISSING_STATE_REASON,
                    row["id"],
                ),
            )
            connection.execute(
                """
                UPDATE collection_subscription_desires desire
                SET desired_state = 'unsubscribed',
                    generation = CASE
                      WHEN desire.desired_state = 'subscribed' THEN desire.generation + 1
                      ELSE desire.generation
                    END,
                    updated_at = %s
                FROM collection_target_specs spec
                WHERE desire.target_spec_id = spec.id AND spec.market_id = %s
                """,
                (observed_at, row["id"]),
            )
            self._transition_market_availability_coverage(
                connection,
                market_id=int(row["id"]),
                changed_at=observed_at,
                unavailable_reason=CATALOG_MISSING_STATE_REASON,
            )
            if row["legacy_instrument_id"] is not None:
                instrument_id = int(row["legacy_instrument_id"])
                self._transition_market_backfill_work(
                    connection,
                    instrument_id=instrument_id,
                    state="paused",
                    changed_at=observed_at,
                )
                connection.execute(
                    """
                    UPDATE collection_targets
                    SET status = 'inactive', deactivated_at = %s, updated_at = %s
                    WHERE instrument_id = %s
                    """,
                    (observed_at, observed_at, instrument_id),
                )
        return count

    def _ensure_legacy_collection_target(
        self,
        connection: psycopg.Connection[Any],
        instrument_id: int,
        *,
        active: bool,
        observed_at: datetime,
    ) -> int:
        row = connection.execute(
            """
            INSERT INTO collection_targets (
              instrument_id, status, activated_at, deactivated_at,
              target_order, candidate_status
            )
            VALUES (%s, %s, %s, %s, NULL, 'in_universe')
            ON CONFLICT (instrument_id) DO UPDATE SET
              status = excluded.status,
              activated_at = CASE
                WHEN excluded.status = 'active'
                  THEN COALESCE(collection_targets.activated_at, excluded.activated_at)
                ELSE collection_targets.activated_at
              END,
              deactivated_at = excluded.deactivated_at,
              updated_at = now()
            RETURNING id
            """,
            (
                instrument_id,
                "active" if active else "inactive",
                observed_at if active else None,
                None if active else observed_at,
            ),
        ).fetchone()
        assert row is not None
        return int(row["id"])

    def _ensure_legacy_collection_plan(
        self,
        connection: psycopg.Connection[Any],
        instrument_id: int,
        *,
        observed_at: datetime,
    ) -> None:
        connection.execute(
            """
            INSERT INTO collection_plans (
              instrument_id, preset, range_start_at, range_end_at,
              is_continuous, method, status
            )
            VALUES (
              %s, 'default-krw-2024', '2024-01-01T00:00:00Z', NULL,
              true, 'safe_restart', 'latest_collecting'
            )
            ON CONFLICT (instrument_id) DO UPDATE SET
              range_start_at = LEAST(
                collection_plans.range_start_at, excluded.range_start_at
              ),
              is_continuous = true,
              method = 'safe_restart',
              status = CASE
                WHEN collection_plans.status IN ('paused', 'stopped')
                  THEN collection_plans.status
                ELSE 'latest_collecting'
              END,
              updated_at = %s
            """,
            (instrument_id, observed_at),
        )

    def _ensure_backfill_job(
        self,
        connection: psycopg.Connection[Any],
        *,
        target_spec_id: int,
        instrument_id: int,
        market_code: str,
        start_at: datetime,
        end_at: datetime,
        priority: int,
        idempotency_key: str | None = None,
    ) -> bool:
        if start_at >= end_at:
            return False
        connection.execute(
            "SELECT pg_advisory_xact_lock(%s, %s)",
            (INSTRUMENT_ADVISORY_LOCK_NAMESPACE, instrument_id),
        )
        active_job = connection.execute(
            """
            SELECT 1
            FROM backfill_jobs job
            WHERE job.status IN (
              'planned', 'pending', 'leased', 'running', 'retry_wait', 'paused'
            )
              AND (
                job.plan -> 'targets' @> %s
                OR EXISTS (
                  SELECT 1 FROM backfill_job_targets target
                  WHERE target.backfill_job_id = job.id AND target.instrument_id = %s
                )
              )
            LIMIT 1
            """,
            (Jsonb([instrument_id]), instrument_id),
        ).fetchone()
        if active_job is not None:
            return False
        if idempotency_key is None:
            completed = connection.execute(
                """
                SELECT max(job.target_end_at) AS target_end_at
                FROM backfill_jobs job
                JOIN backfill_job_targets target ON target.backfill_job_id = job.id
                WHERE target.target_spec_id = %s AND job.status = 'succeeded'
                """,
                (target_spec_id,),
            ).fetchone()
            if completed is not None and completed["target_end_at"] is not None:
                start_at = max(start_at, cast(datetime, completed["target_end_at"]))
            if start_at >= end_at:
                return False
            idempotency_key = ":".join(
                (
                    "p1",
                    "default",
                    str(target_spec_id),
                    "source_candle",
                    "1m",
                    start_at.isoformat(),
                    end_at.isoformat(),
                )
            )
        duration_minutes = max(1, int((end_at - start_at).total_seconds() // 60))
        row = connection.execute(
            """
            INSERT INTO backfill_jobs (
              status, data_type, plan, target_start_at, target_end_at,
              estimated_request_count, estimated_row_count, estimated_storage_bytes,
              restart_mode, created_by, approved_by, approved_at,
              idempotency_key, priority
            )
            VALUES (
              'pending', 'source_candle', %s, %s, %s, %s, %s, %s,
              'safe_restart', 'system', 'system', now(), %s, %s
            )
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING id
            """,
            (
                Jsonb(
                    {
                        "planId": idempotency_key,
                        "targets": [instrument_id],
                        "targetSpecId": target_spec_id,
                        "marketCode": market_code,
                        "automatic": True,
                    }
                ),
                start_at,
                end_at,
                ceil(duration_minutes / 200),
                duration_minutes,
                duration_minutes * 256,
                idempotency_key,
                priority,
            ),
        ).fetchone()
        if row is None:
            return False
        connection.execute(
            """
            INSERT INTO backfill_job_targets (
              backfill_job_id, instrument_id, status, target_spec_id
            )
            VALUES (%s, %s, 'pending', %s)
            ON CONFLICT (backfill_job_id, instrument_id) DO NOTHING
            """,
            (row["id"], instrument_id, target_spec_id),
        )
        return True

    def overview(self) -> DataFoundationOverview:
        with self._connect() as connection:
            totals = connection.execute(
                """
                SELECT
                  (SELECT count(*) FROM markets) AS market_count,
                  (SELECT count(*) FROM markets WHERE quote_currency = 'KRW')
                    AS krw_market_count,
                  (SELECT count(*) FROM collection_target_specs WHERE status = 'active')
                    AS active_target_count,
                  (SELECT count(*) FROM backfill_jobs
                    WHERE status IN ('pending', 'leased', 'running', 'retry_wait'))
                    AS pending_backfill_job_count,
                  (SELECT count(*) FROM collection_subscription_desires
                    WHERE desired_state = 'subscribed')
                    AS desired_subscription_count,
                  (SELECT default_start_at FROM collection_policies
                    WHERE exchange = 'UPBIT' AND quote_currency = 'KRW'
                      AND name = %s) AS policy_start_at
                """,
                (DEFAULT_POLICY_NAME,),
            ).fetchone()
            assert totals is not None
            coverage_counts = self._coverage_counts(connection)
        return DataFoundationOverview(
            market_count=int(totals["market_count"]),
            krw_market_count=int(totals["krw_market_count"]),
            active_target_count=int(totals["active_target_count"]),
            pending_backfill_job_count=int(totals["pending_backfill_job_count"]),
            desired_subscription_count=int(totals["desired_subscription_count"]),
            policy_start_at=cast(datetime, totals["policy_start_at"]),
            coverage_counts=coverage_counts,
            markets=self.list_markets(),
        )

    def list_markets(self) -> list[MarketCollectionStatus]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                  market.market_code,
                  market.korean_name,
                  market.english_name,
                  market.quote_currency,
                  COALESCE(history.trading_status, 'unknown') AS trading_status,
                  COALESCE(history.market_warning, 'NONE') AS market_warning,
                  count(spec.id) FILTER (
                    WHERE spec.exclusion_reason IS DISTINCT FROM
                      'policy:data-type-disabled'
                  ) AS total_data_type_count,
                  count(spec.id) FILTER (
                    WHERE spec.status = 'active'
                      AND spec.exclusion_reason IS DISTINCT FROM
                        'policy:data-type-disabled'
                  )
                    AS active_data_type_count,
                  CASE
                    WHEN count(spec.id) FILTER (
                      WHERE spec.exclusion_reason IS DISTINCT FROM
                        'policy:data-type-disabled'
                    ) = 0 THEN 'not_targeted'
                    WHEN bool_or(spec.status = 'excluded') FILTER (
                      WHERE spec.exclusion_reason IS DISTINCT FROM
                        'policy:data-type-disabled'
                    ) THEN 'excluded'
                    WHEN bool_or(spec.status = 'active') FILTER (
                      WHERE spec.exclusion_reason IS DISTINCT FROM
                        'policy:data-type-disabled'
                    ) THEN 'active'
                    ELSE 'paused'
                  END AS target_status,
                  min(spec.range_start_at) FILTER (
                    WHERE spec.exclusion_reason IS DISTINCT FROM
                      'policy:data-type-disabled'
                  ) AS policy_start_at,
                  array_agg(spec.data_type ORDER BY CASE spec.data_type
                    WHEN 'source_candle' THEN 1
                    WHEN 'trade_event' THEN 2
                    WHEN 'orderbook_snapshot' THEN 3
                    WHEN 'ticker_snapshot' THEN 4
                    ELSE 5 END) FILTER (
                    WHERE spec.exclusion_reason IS DISTINCT FROM
                      'policy:data-type-disabled'
                  ) AS policy_data_types,
                  max(spec.candle_unit) FILTER (
                    WHERE spec.data_type = 'source_candle'
                      AND spec.exclusion_reason IS DISTINCT FROM
                        'policy:data-type-disabled'
                  ) AS policy_candle_unit,
                  max(spec.retention_days) FILTER (
                    WHERE spec.exclusion_reason IS DISTINCT FROM
                      'policy:data-type-disabled'
                  ) AS policy_retention_days,
                  max(spec.priority) FILTER (
                    WHERE spec.exclusion_reason IS DISTINCT FROM
                      'policy:data-type-disabled'
                  ) AS policy_priority,
                  bool_and(spec.continuous) FILTER (
                    WHERE spec.exclusion_reason IS DISTINCT FROM
                      'policy:data-type-disabled'
                  ) AS policy_continuous
                FROM markets market
                LEFT JOIN market_status_history history
                  ON history.market_id = market.id AND history.valid_to IS NULL
                LEFT JOIN collection_target_specs spec ON spec.market_id = market.id
                GROUP BY
                  market.id, history.trading_status, history.market_warning
                ORDER BY market.quote_currency, market.market_code
                """
            ).fetchall()
            coverage_by_market = self._coverage_counts_by_market(connection)
        return [
            MarketCollectionStatus(
                market_code=str(row["market_code"]),
                korean_name=str(row["korean_name"]),
                english_name=str(row["english_name"]),
                quote_currency=str(row["quote_currency"]),
                trading_status=cast(Any, row["trading_status"]),
                market_warning=str(row["market_warning"]),
                target_status=cast(Any, row["target_status"]),
                active_data_type_count=int(row["active_data_type_count"]),
                total_data_type_count=int(row["total_data_type_count"]),
                coverage_counts=coverage_by_market.get(
                    str(row["market_code"]), _empty_coverage_counts()
                ),
                collection_policy=(
                    MarketCollectionPolicySettings(
                        start_at=cast(datetime, row["policy_start_at"]),
                        data_types=cast(Any, tuple(row["policy_data_types"])),
                        candle_unit=cast(Any, row["policy_candle_unit"] or "1m"),
                        retention_days=cast(int | None, row["policy_retention_days"]),
                        priority=int(row["policy_priority"]),
                        continuous=bool(row["policy_continuous"]),
                    )
                    if row["policy_start_at"] is not None
                    else None
                ),
            )
            for row in rows
        ]

    def market_history(self, market_code: str) -> list[MarketStatusRevision]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT market.market_code, history.*
                FROM market_status_history history
                JOIN markets market ON market.id = history.market_id
                WHERE market.exchange = 'UPBIT' AND market.market_code = %s
                ORDER BY history.valid_from
                """,
                (market_code,),
            ).fetchall()
        return [
            MarketStatusRevision(
                market_code=str(row["market_code"]),
                trading_status=cast(Any, row["trading_status"]),
                market_warning=str(row["market_warning"]),
                valid_from=cast(datetime, row["valid_from"]),
                valid_to=cast(datetime | None, row["valid_to"]),
                observed_at=cast(datetime, row["observed_at"]),
            )
            for row in rows
        ]

    def exclude_market(
        self,
        market_code: str,
        *,
        actor: str,
        reason: str,
        changed_at: datetime,
    ) -> None:
        self.set_market_target_state(
            market_code,
            state="excluded",
            actor=actor,
            reason=reason,
            changed_at=changed_at,
        )

    def set_market_target_state(
        self,
        market_code: str,
        *,
        state: str,
        actor: str,
        reason: str,
        changed_at: datetime,
        policy: MarketCollectionPolicySettings | None = None,
    ) -> None:
        _require_utc(changed_at, "changed_at")
        if state not in {"active", "paused", "excluded"}:
            raise ValueError("지원하지 않는 수집 대상 상태다.")
        if not actor or not reason:
            raise ValueError("수집 대상 변경 actor와 reason은 필수다.")
        if policy is not None:
            policy.validate(changed_at=changed_at)
        with self._connect() as connection:
            market = connection.execute(
                """
                SELECT market.id, market.legacy_instrument_id,
                       COALESCE(history.trading_status, 'unknown') AS trading_status
                FROM markets market
                LEFT JOIN market_status_history history
                  ON history.market_id = market.id AND history.valid_to IS NULL
                WHERE market.exchange = 'UPBIT' AND market.market_code = %s
                FOR UPDATE OF market
                """,
                (market_code,),
            ).fetchone()
            if market is None:
                raise ValueError("변경할 시장을 찾을 수 없다.")
            if state == "active" and market["trading_status"] != "active":
                raise ValueError("거래 중단 시장은 수집 활성화할 수 없다.")
            specifications = connection.execute(
                """
                SELECT * FROM collection_target_specs
                WHERE market_id = %s
                ORDER BY id
                FOR UPDATE
                """,
                (market["id"],),
            ).fetchall()
            if not specifications:
                raise ValueError("해당 시장에 적용할 KRW 기본 정책이 없다.")
            selected_data_types = (
                set(policy.data_types)
                if policy is not None
                else {
                    str(specification["data_type"])
                    for specification in specifications
                    if specification["exclusion_reason"] != POLICY_DISABLED_REASON
                }
            )
            source_specification: Row | None = None
            for specification in specifications:
                data_type = str(specification["data_type"])
                selected_before = specification["exclusion_reason"] != POLICY_DISABLED_REASON
                selected_after = data_type in selected_data_types
                previous_configuration = (
                    specification["range_start_at"],
                    specification["candle_unit"],
                    specification["retention_days"],
                    specification["priority"],
                    specification["continuous"],
                )
                next_configuration = (
                    policy.start_at if policy is not None else specification["range_start_at"],
                    (
                        policy.candle_unit
                        if policy is not None and data_type == "source_candle"
                        else specification["candle_unit"]
                    ),
                    (
                        policy.retention_days
                        if policy is not None
                        else specification["retention_days"]
                    ),
                    policy.priority if policy is not None else specification["priority"],
                    (policy.continuous if policy is not None else specification["continuous"]),
                )
                configuration_changed = previous_configuration != next_configuration
                policy_changed = selected_before != selected_after or (
                    selected_after and configuration_changed
                )
                next_status = state if selected_after else "paused"
                next_exclusion_reason = (
                    POLICY_DISABLED_REASON
                    if not selected_after
                    else reason
                    if state == "excluded"
                    else None
                )
                next_state_reason = (
                    POLICY_DISABLED_STATE_REASON
                    if not selected_after
                    else None
                    if state == "active"
                    else OPERATOR_PAUSED_STATE_REASON
                    if state == "paused"
                    else OPERATOR_EXCLUDED_STATE_REASON
                )
                connection.execute(
                    """
                    UPDATE collection_target_specs
                    SET range_start_at = %s,
                        candle_unit = %s,
                        retention_days = %s,
                        priority = %s,
                        continuous = %s,
                        auto_managed = false,
                        status = %s,
                        state_reason = %s,
                        excluded_by = CASE WHEN %s = 'excluded' THEN %s ELSE NULL END,
                        exclusion_reason = %s,
                        updated_at = %s
                    WHERE id = %s
                    """,
                    (
                        *next_configuration,
                        next_status,
                        next_state_reason,
                        next_status,
                        actor,
                        next_exclusion_reason,
                        changed_at,
                        specification["id"],
                    ),
                )
                if data_type == "source_candle":
                    source_specification = {**specification, "candle_unit": next_configuration[1]}
                next_desired_state = (
                    "subscribed"
                    if selected_after and state == "active" and bool(next_configuration[4])
                    else "unsubscribed"
                )
                desire = connection.execute(
                    """
                    SELECT desired_state FROM collection_subscription_desires
                    WHERE target_spec_id = %s
                    FOR UPDATE
                    """,
                    (specification["id"],),
                ).fetchone()
                if desire is None:
                    connection.execute(
                        """
                        INSERT INTO collection_subscription_desires (
                          target_spec_id, desired_state
                        ) VALUES (%s, %s)
                        """,
                        (specification["id"], next_desired_state),
                    )
                else:
                    advance_generation = (
                        str(desire["desired_state"]) != next_desired_state or policy_changed
                    )
                    connection.execute(
                        """
                        UPDATE collection_subscription_desires
                        SET desired_state = %s,
                            generation = generation + %s,
                            updated_at = %s
                        WHERE target_spec_id = %s
                        """,
                        (
                            next_desired_state,
                            1 if advance_generation else 0,
                            changed_at,
                            specification["id"],
                        ),
                    )
            if market["legacy_instrument_id"] is not None:
                self._transition_market_backfill_work(
                    connection,
                    instrument_id=int(market["legacy_instrument_id"]),
                    state=state,
                    changed_at=changed_at,
                )
            if (
                policy is not None
                and "source_candle" in selected_data_types
                and state == "active"
                and source_specification is not None
                and market["legacy_instrument_id"] is not None
            ):
                policy_payload = json.dumps(
                    {
                        "targetSpecId": source_specification["id"],
                        "startAt": policy.start_at.isoformat(),
                        "candleUnit": policy.candle_unit,
                        "retentionDays": policy.retention_days,
                        "priority": policy.priority,
                        "continuous": policy.continuous,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                policy_key = hashlib.sha256(policy_payload.encode()).hexdigest()[:20]
                self._ensure_backfill_job(
                    connection,
                    target_spec_id=int(source_specification["id"]),
                    instrument_id=int(market["legacy_instrument_id"]),
                    market_code=market_code,
                    start_at=policy.start_at,
                    end_at=changed_at,
                    priority=policy.priority,
                    idempotency_key=(f"p1:policy:{source_specification['id']}:{policy_key}"),
                )
            if market["legacy_instrument_id"] is not None:
                connection.execute(
                    """
                    UPDATE collection_targets
                    SET status = %s,
                        activated_at = CASE
                          WHEN %s = 'active' THEN COALESCE(activated_at, %s)
                          ELSE activated_at
                        END,
                        deactivated_at = CASE WHEN %s = 'active' THEN NULL ELSE %s END,
                        updated_at = %s
                    WHERE instrument_id = %s
                    """,
                    (
                        "active" if state == "active" else "inactive",
                        state,
                        changed_at,
                        state,
                        changed_at,
                        changed_at,
                        market["legacy_instrument_id"],
                    ),
                )
                if policy is not None:
                    connection.execute(
                        """
                        UPDATE collection_plans
                        SET range_start_at = %s,
                            is_continuous = %s,
                            updated_at = %s
                        WHERE instrument_id = %s
                        """,
                        (
                            policy.start_at,
                            policy.continuous,
                            changed_at,
                            market["legacy_instrument_id"],
                        ),
                    )
            connection.execute(
                """
                INSERT INTO audit_logs (
                  actor, action, target_type, target_id, after_data, created_at
                )
                VALUES ('local_user', 'market_target_state_changed', 'market', %s, %s, %s)
                """,
                (
                    market_code,
                    Jsonb(
                        {
                            "actorId": actor,
                            "state": state,
                            "reason": reason,
                            "policy": (
                                {
                                    "startAt": policy.start_at.isoformat(),
                                    "dataTypes": list(policy.data_types),
                                    "candleUnit": policy.candle_unit,
                                    "retentionDays": policy.retention_days,
                                    "priority": policy.priority,
                                    "continuous": policy.continuous,
                                }
                                if policy is not None
                                else None
                            ),
                        }
                    ),
                    changed_at,
                ),
            )

    def _transition_market_backfill_work(
        self,
        connection: psycopg.Connection[Any],
        *,
        instrument_id: int,
        state: str,
        changed_at: datetime,
    ) -> None:
        if state == "active":
            eligible_rows = connection.execute(
                """
                SELECT job.id
                FROM backfill_jobs job
                WHERE job.status = 'paused'
                  AND (
                    EXISTS (
                      SELECT 1 FROM backfill_job_targets target
                      WHERE target.backfill_job_id = job.id
                        AND target.instrument_id = %s
                    )
                    OR job.plan -> 'targets' @> %s
                  )
                  AND EXISTS (
                    SELECT 1 FROM backfill_job_targets target
                    WHERE target.backfill_job_id = job.id
                  )
                  AND NOT EXISTS (
                    SELECT 1
                    FROM backfill_job_targets paused_target
                    WHERE paused_target.backfill_job_id = job.id
                      AND paused_target.status = 'paused'
                      AND NOT EXISTS (
                        SELECT 1
                        FROM markets market
                        JOIN collection_target_specs spec
                          ON spec.market_id = market.id
                        WHERE market.legacy_instrument_id = paused_target.instrument_id
                          AND spec.data_type = job.data_type
                          AND spec.status = 'active'
                          AND spec.exclusion_reason IS DISTINCT FROM %s
                      )
                  )
                ORDER BY job.id
                FOR UPDATE OF job
                """,
                (instrument_id, Jsonb([instrument_id]), POLICY_DISABLED_REASON),
            ).fetchall()
            eligible_job_ids = [int(row["id"]) for row in eligible_rows]
            if not eligible_job_ids:
                return
            connection.execute(
                """
                UPDATE backfill_job_targets
                SET status = 'pending', updated_at = %s
                WHERE backfill_job_id = ANY(%s) AND status = 'paused'
                """,
                (changed_at, eligible_job_ids),
            )
            connection.execute(
                """
                UPDATE backfill_jobs
                SET status = 'pending', updated_at = %s
                WHERE id = ANY(%s) AND status = 'paused'
                """,
                (changed_at, eligible_job_ids),
            )
            return

        job_status = "paused" if state == "paused" else "cancelled"
        target_status = "paused" if state == "paused" else "stopped"
        connection.execute(
            """
            UPDATE backfill_job_targets target
            SET status = %s, updated_at = %s
            WHERE target.backfill_job_id IN (
              SELECT job.id
              FROM backfill_jobs job
              WHERE job.status IN (
                'pending', 'leased', 'running', 'retry_wait', 'paused'
              )
                AND (
                  job.plan -> 'targets' @> %s
                  OR EXISTS (
                    SELECT 1 FROM backfill_job_targets affected
                    WHERE affected.backfill_job_id = job.id
                      AND affected.instrument_id = %s
                  )
                )
            )
              AND target.status IN ('pending', 'running', 'paused')
            """,
            (target_status, changed_at, Jsonb([instrument_id]), instrument_id),
        )
        connection.execute(
            """
            UPDATE backfill_jobs job
            SET status = %s,
                lease_owner = NULL,
                lease_expires_at = NULL,
                updated_at = %s
            WHERE job.status IN (
              'pending', 'leased', 'running', 'retry_wait', 'paused'
            )
              AND (
                EXISTS (
                  SELECT 1 FROM backfill_job_targets target
                  WHERE target.backfill_job_id = job.id
                    AND target.instrument_id = %s
                )
                OR job.plan -> 'targets' @> %s
              )
            """,
            (job_status, changed_at, instrument_id, Jsonb([instrument_id])),
        )

    def claim_backfill_job(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> LeasedBackfillJob | None:
        _require_utc(now, "now")
        if not worker_id:
            raise ValueError("worker_id는 필수다.")
        if lease_seconds < 1:
            raise ValueError("lease_seconds는 1 이상이어야 한다.")
        lease_expires_at = now + timedelta(seconds=lease_seconds)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM backfill_jobs
                WHERE
                  status = 'pending'
                  OR (status = 'retry_wait' AND COALESCE(next_retry_at, '-infinity') <= %s)
                  OR (
                    status IN ('leased', 'running')
                    AND lease_expires_at IS NOT NULL
                    AND lease_expires_at <= %s
                  )
                ORDER BY priority DESC, created_at, id
                LIMIT 1
                FOR UPDATE SKIP LOCKED
                """,
                (now, now),
            ).fetchone()
            if row is None:
                return None
            if int(row["attempt_count"]) >= int(row["max_attempts"]):
                connection.execute(
                    """
                    UPDATE backfill_jobs
                    SET status = 'dead_letter',
                        dead_letter_reason = COALESCE(
                          dead_letter_reason, 'lease recovery attempt budget exhausted'
                        ),
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        updated_at = %s
                    WHERE id = %s
                    """,
                    (now, row["id"]),
                )
                return None
            claimed = connection.execute(
                """
                UPDATE backfill_jobs
                SET status = 'running',
                    lease_owner = %s,
                    lease_expires_at = %s,
                    attempt_count = attempt_count + 1,
                    started_at = COALESCE(started_at, %s),
                    updated_at = %s
                WHERE id = %s
                RETURNING *
                """,
                (worker_id, lease_expires_at, now, now, row["id"]),
            ).fetchone()
            assert claimed is not None
        return LeasedBackfillJob(
            id=int(claimed["id"]),
            idempotency_key=str(claimed["idempotency_key"]),
            lease_owner=str(claimed["lease_owner"]),
            lease_expires_at=cast(datetime, claimed["lease_expires_at"]),
            attempt_count=int(claimed["attempt_count"]),
            max_attempts=int(claimed["max_attempts"]),
            target_start_at=cast(datetime, claimed["target_start_at"]),
            target_end_at=cast(datetime, claimed["target_end_at"]),
        )

    def _coverage_counts(self, connection: psycopg.Connection[Any]) -> dict[CoverageState, int]:
        rows = connection.execute(
            "SELECT status, count(*) AS count FROM coverage_intervals GROUP BY status"
        ).fetchall()
        counts = _empty_coverage_counts()
        for row in rows:
            counts[cast(CoverageState, row["status"])] = int(row["count"])
        return counts

    def _coverage_counts_by_market(
        self, connection: psycopg.Connection[Any]
    ) -> dict[str, dict[CoverageState, int]]:
        rows = connection.execute(
            """
            SELECT market.market_code, coverage.status, count(*) AS count
            FROM coverage_intervals coverage
            JOIN collection_target_specs spec ON spec.id = coverage.target_spec_id
            JOIN markets market ON market.id = spec.market_id
            GROUP BY market.market_code, coverage.status
            """
        ).fetchall()
        result: dict[str, dict[CoverageState, int]] = {}
        for row in rows:
            market_counts = result.setdefault(str(row["market_code"]), _empty_coverage_counts())
            market_counts[cast(CoverageState, row["status"])] = int(row["count"])
        return result


def _empty_coverage_counts() -> dict[CoverageState, int]:
    return {
        "available": 0,
        "no_trade": 0,
        "missing": 0,
        "unavailable": 0,
        "unverified": 0,
    }
