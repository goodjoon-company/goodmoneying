from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from psycopg import errors
from psycopg.types.json import Jsonb

from goodmoneying_shared.backtest_engine import (
    BacktestCandleEvent,
    BacktestEngineSpec,
    BacktestResult,
    ExecutionModel,
    run_candle_backtest,
)

Row = dict[str, Any]


class BacktestIdempotencyConflictError(ValueError):
    """같은 멱등 키가 다른 백테스트 저장 요청 본문을 가리킨다."""


class BacktestInputNotReadyError(ValueError):
    """백테스트 실행 입력으로 쓸 published 전략 또는 sealed 데이터셋이 없다."""


class BacktestCursorMismatchError(ValueError):
    """백테스트 목록 cursor가 현재 조회 문맥과 맞지 않는다."""


class BacktestLeaseLostError(RuntimeError):
    """백테스트 run 임대가 만료됐거나 다른 worker로 이동했다."""


_LIST_CURSOR_KIND = "backtest-run-list-v1"
_TRADE_CURSOR_KIND = "backtest-trade-list-v1"
_EQUITY_CURSOR_KIND = "backtest-equity-list-v1"
_CURSOR_HMAC_SECRET = os.getenv("GOODMONEYING_CURSOR_HMAC_SECRET") or secrets.token_hex(32)


class PostgresBacktestStore:
    def __init__(self, repository: object) -> None:
        self._repository = repository

    def create_run(self, **arguments: object) -> Row:
        return create_run(self._repository, **arguments)

    def persist_completed_run(self, **arguments: object) -> Row:
        return persist_completed_run(self._repository, **arguments)

    def get_run(self, backtest_run_id: int) -> Row | None:
        return get_run(self._repository, backtest_run_id)

    def list_runs(self, **arguments: object) -> Row:
        return list_runs(self._repository, **arguments)

    def list_run_trades(self, **arguments: object) -> Row | None:
        return list_run_trades(self._repository, **arguments)

    def list_run_equity_points(self, **arguments: object) -> Row | None:
        return list_run_equity_points(self._repository, **arguments)

    def claim_next_run(self, worker_id: str, lease_seconds: int = 120) -> Row | None:
        return claim_next_run(
            self._repository,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
        )

    def complete_claimed_run(
        self,
        backtest_run_id: int,
        worker_id: str,
        lease_generation: int,
        *,
        result: BacktestResult,
        artifacts: object = (),
        completed_at: object | None = None,
    ) -> Row:
        return complete_claimed_run(
            self._repository,
            backtest_run_id=backtest_run_id,
            worker_id=worker_id,
            lease_generation=lease_generation,
            result=result,
            artifacts=artifacts,
            completed_at=completed_at,
        )

    def fail_claimed_run(
        self,
        backtest_run_id: int,
        worker_id: str,
        lease_generation: int,
        *,
        error_code: str,
        message: str,
    ) -> Row:
        return fail_claimed_run(
            self._repository,
            backtest_run_id=backtest_run_id,
            worker_id=worker_id,
            lease_generation=lease_generation,
            error_code=error_code,
            message=message,
        )


def claim_next_run(
    repository: object,
    *,
    worker_id: str,
    lease_seconds: int = 120,
) -> Row | None:
    owner = _non_blank(worker_id, "workerId")
    if not isinstance(lease_seconds, int) or lease_seconds < 1:
        raise ValueError("leaseSeconds는 1 이상 정수여야 한다.")
    with _connector(repository)() as connection:
        connection.execute(
            """
            UPDATE backtest_runs SET status='dead_letter', lease_owner=NULL,
              lease_expires_at=NULL, next_retry_at=NULL,
              dead_letter_reason='retry_attempts_exhausted',
              finished_at=clock_timestamp()
            WHERE status='running' AND lease_expires_at <= clock_timestamp()
              AND attempt_count >= max_attempts
            """
        )
        run = connection.execute(
            """
            SELECT * FROM backtest_runs
            WHERE attempt_count < max_attempts AND (
              status='queued'
              OR (status='retry_wait' AND next_retry_at <= clock_timestamp())
              OR (status='running' AND lease_expires_at <= clock_timestamp())
            )
            ORDER BY requested_at, id FOR UPDATE SKIP LOCKED LIMIT 1
            """
        ).fetchone()
        if run is None:
            return None
        generation = int(cast(int, run["lease_generation"])) + 1
        claimed = connection.execute(
            """
            UPDATE backtest_runs SET status='running', lease_owner=%s,
              lease_expires_at=clock_timestamp() + make_interval(secs => %s),
              lease_generation=%s, attempt_count=attempt_count+1,
              next_retry_at=NULL, dead_letter_reason=NULL,
              started_at=COALESCE(started_at, clock_timestamp()), finished_at=NULL
            WHERE id=%s AND lease_generation=%s
            RETURNING *
            """,
            (owner, lease_seconds, generation, run["id"], run["lease_generation"]),
        ).fetchone()
        if claimed is None:
            return None
        return _claimed_run_response(claimed)


def complete_claimed_run(
    repository: object,
    *,
    backtest_run_id: int,
    worker_id: str,
    lease_generation: int,
    result: BacktestResult,
    artifacts: object = (),
    completed_at: object | None = None,
) -> Row:
    owner = _non_blank(worker_id, "workerId")
    if not isinstance(backtest_run_id, int) or backtest_run_id < 1:
        raise ValueError("backtestRunId는 1 이상 정수여야 한다.")
    if not isinstance(lease_generation, int) or lease_generation < 1:
        raise ValueError("leaseGeneration은 1 이상 정수여야 한다.")
    if not isinstance(result, BacktestResult):
        raise ValueError("백테스트 result가 필요하다.")
    artifact_rows = _artifact_inputs(artifacts)
    completed_at_dt = (
        _datetime(completed_at, "completedAt")
        if completed_at is not None
        else datetime.now(UTC)
    )
    with _connector(repository)() as connection:
        run = connection.execute(
            """
            SELECT * FROM backtest_runs
            WHERE id=%s AND status='running' AND lease_owner=%s
              AND lease_generation=%s AND lease_expires_at > clock_timestamp()
            FOR UPDATE
            """,
            (backtest_run_id, owner, lease_generation),
        ).fetchone()
        if run is None:
            raise BacktestLeaseLostError("백테스트 run 임대가 현재 worker에 속하지 않는다.")
        if run["input_hash"] != result.input_hash:
            raise ValueError("백테스트 result input_hash가 임대 run과 다르다.")

        _insert_trades(connection, backtest_run_id, result)
        _insert_equity_points(connection, backtest_run_id, result)
        _insert_metrics(connection, backtest_run_id, result)
        _insert_artifacts(connection, backtest_run_id, artifact_rows)
        completed = connection.execute(
            """
            UPDATE backtest_runs SET status=%s, result_hash=%s, assumptions=%s,
              lease_owner=NULL, lease_expires_at=NULL, finished_at=%s
            WHERE id=%s AND status='running' AND lease_owner=%s
              AND lease_generation=%s AND lease_expires_at > clock_timestamp()
            RETURNING *
            """,
            (
                result.status,
                result.result_hash,
                Jsonb(list(result.assumptions)),
                completed_at_dt,
                backtest_run_id,
                owner,
                lease_generation,
            ),
        ).fetchone()
        if completed is None:
            raise BacktestLeaseLostError("백테스트 run 완료 전 임대를 잃었다.")
        saved = _get_run_with_connection(connection, backtest_run_id)
        assert saved is not None
        return saved


def fail_claimed_run(
    repository: object,
    *,
    backtest_run_id: int,
    worker_id: str,
    lease_generation: int,
    error_code: str,
    message: str,
) -> Row:
    owner = _non_blank(worker_id, "workerId")
    code = _non_blank(error_code, "errorCode")
    failure_message = _non_blank(message, "message")
    if not isinstance(backtest_run_id, int) or backtest_run_id < 1:
        raise ValueError("backtestRunId는 1 이상 정수여야 한다.")
    if not isinstance(lease_generation, int) or lease_generation < 1:
        raise ValueError("leaseGeneration은 1 이상 정수여야 한다.")
    with _connector(repository)() as connection:
        failed = connection.execute(
            """
            UPDATE backtest_runs SET
              status=CASE WHEN attempt_count >= max_attempts
                          THEN 'dead_letter' ELSE 'retry_wait' END,
              lease_owner=NULL, lease_expires_at=NULL,
              next_retry_at=CASE WHEN attempt_count >= max_attempts THEN NULL
                ELSE clock_timestamp() + make_interval(
                  secs => LEAST(300, power(2, attempt_count)::integer)
                ) END,
              last_error_code=%s,
              last_error_message=%s,
              dead_letter_reason=CASE WHEN attempt_count >= max_attempts
                THEN %s ELSE NULL END,
              finished_at=CASE WHEN attempt_count >= max_attempts
                THEN clock_timestamp() ELSE NULL END
            WHERE id=%s AND status='running' AND lease_owner=%s
              AND lease_generation=%s AND lease_expires_at > clock_timestamp()
            RETURNING *
            """,
            (code, failure_message, code, backtest_run_id, owner, lease_generation),
        ).fetchone()
        if failed is None:
            raise BacktestLeaseLostError("백테스트 run 실패 전이를 쓸 임대를 잃었다.")
        return _claimed_run_response(failed)


def create_run(
    repository: object,
    *,
    request_id: object,
    idempotency_key: object,
    actor_id: object,
    requested_at: object,
    reason: object,
    strategy_version_id: object,
    dataset_version_id: object,
    engine_version: object,
    parameters: object,
    seed: object,
    initial_cash: object,
    execution: object,
    max_attempts: object = 3,
) -> Row:
    requested_at_dt = _datetime(requested_at, "requestedAt")
    command_payload = {
        "requestId": _non_blank(request_id, "requestId"),
        "idempotencyKey": _non_blank(idempotency_key, "idempotencyKey"),
        "actorId": _non_blank(actor_id, "actorId"),
        "requestedAt": requested_at_dt.isoformat(),
        "reason": _non_blank(reason, "reason"),
        "strategyVersionId": _positive_int(strategy_version_id, "strategyVersionId"),
        "datasetVersionId": _positive_int(dataset_version_id, "datasetVersionId"),
        "engineVersion": _non_blank(engine_version, "engineVersion"),
        "parameters": _parameter_payload(parameters),
        "seed": _seed(seed),
        "initialCash": _decimal_text(initial_cash, "initialCash"),
        "execution": _execution_payload(execution),
        "maxAttempts": _positive_int(max_attempts, "maxAttempts"),
    }
    request_hash = _hash(command_payload)
    parameter_hash = _hash(cast(Mapping[str, object], command_payload["parameters"]))
    connector = _connector(repository)
    for attempt in range(3):
        try:
            with connector() as connection:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"backtest-run:{command_payload['idempotencyKey']}",),
                )
                existing = connection.execute(
                    "SELECT id, request_hash FROM backtest_runs WHERE idempotency_key=%s",
                    (command_payload["idempotencyKey"],),
                ).fetchone()
                if existing is not None:
                    if existing["request_hash"] != request_hash:
                        raise BacktestIdempotencyConflictError(
                            "멱등 키의 기존 백테스트 실행 요청과 본문이 다르다."
                        )
                    replay = _get_run_summary_with_connection(
                        connection, int(cast(int, existing["id"]))
                    )
                    assert replay is not None
                    return replay

                material = connection.execute(
                    """
                    SELECT
                      strategy.id AS strategy_version_id,
                      strategy.graph_hash AS strategy_graph_hash,
                      version.id AS dataset_version_id,
                      version.content_hash AS dataset_content_hash,
                      version.as_of AS dataset_as_of,
                      version.output_start_at AS dataset_from,
                      version.end_at AS dataset_to,
                      version.fill_policy,
                      version.missing_policy
                    FROM strategy_versions strategy
                    JOIN strategy_graphs graph
                      ON graph.strategy_version_id=strategy.id
                     AND graph.graph_hash=strategy.graph_hash
                    CROSS JOIN dataset_versions version
                    WHERE strategy.id=%s
                      AND strategy.status='published'
                      AND version.id=%s
                      AND version.sealed_at IS NOT NULL
                    """,
                    (
                        command_payload["strategyVersionId"],
                        command_payload["datasetVersionId"],
                    ),
                ).fetchone()
                if material is None:
                    raise BacktestInputNotReadyError(
                        "published 전략 version과 sealed 데이터셋 version만 "
                        "백테스트 실행에 사용할 수 있다."
                    )

                execution_payload = cast(
                    Mapping[str, object],
                    command_payload["execution"],
                )
                spec = BacktestEngineSpec(
                    dataset_version_id=int(cast(int, material["dataset_version_id"])),
                    dataset_content_hash=str(material["dataset_content_hash"]),
                    strategy_version_id=int(cast(int, material["strategy_version_id"])),
                    strategy_graph_hash=str(material["strategy_graph_hash"]),
                    engine_version=str(command_payload["engineVersion"]),
                    parameter_hash=parameter_hash,
                    seed=int(cast(int, command_payload["seed"])),
                    initial_cash=Decimal(str(command_payload["initialCash"])),
                    execution=ExecutionModel(
                        fee_rate=Decimal(str(execution_payload["feeRate"])),
                        slippage_bps=Decimal(str(execution_payload["slippageBps"])),
                        latency_seconds=int(cast(int, execution_payload["latencySeconds"])),
                        max_participation_rate=Decimal(
                            str(execution_payload["maxParticipationRate"])
                        ),
                    ),
                )
                candle_events = _load_candle_events(
                    connection,
                    int(cast(int, material["dataset_version_id"])),
                )
                preview = run_candle_backtest(spec, candles=candle_events, signals=())
                input_hash = preview.input_hash
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"backtest-input:{input_hash}",),
                )
                input_payload = {
                    "kind": "backtest-run-input-v1",
                    "spec": _engine_spec_payload(spec),
                    "events": [_engine_event_payload(event) for event in candle_events],
                    "signals": [],
                    "assumptions": list(preview.assumptions),
                    "dataset": {
                        "asOf": _json_time(material["dataset_as_of"]),
                        "from": _json_time(material["dataset_from"]),
                        "to": _json_time(material["dataset_to"]),
                        "fillPolicy": material["fill_policy"],
                        "missingPolicy": material["missing_policy"],
                    },
                }
                duplicate = connection.execute(
                    """
                    SELECT
                      id, strategy_version_id, dataset_version_id, engine_version,
                      status, input_hash, result_hash, requested_at, started_at, finished_at
                    FROM backtest_runs WHERE input_hash=%s
                    """,
                    (input_hash,),
                ).fetchone()
                if duplicate is not None:
                    raise BacktestIdempotencyConflictError(
                        "동일한 백테스트 입력이 이미 다른 멱등 키로 생성됐다."
                    )

                run = connection.execute(
                    """
                    INSERT INTO backtest_runs (
                      strategy_version_id, strategy_graph_hash,
                      dataset_version_id, dataset_content_hash,
                      engine_version, status, input_hash, input_payload, result_hash,
                      parameter_hash, seed, assumptions, idempotency_key,
                      request_id, actor_id, requested_at, reason, request_hash,
                      max_attempts
                    ) VALUES (
                      %s,%s,%s,%s,%s,'queued',%s,%s,NULL,
                      %s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                    ) RETURNING
                      id, strategy_version_id, dataset_version_id, engine_version,
                      status, input_hash, result_hash, requested_at, started_at, finished_at
                    """,
                    (
                        material["strategy_version_id"],
                        material["strategy_graph_hash"],
                        material["dataset_version_id"],
                        material["dataset_content_hash"],
                        command_payload["engineVersion"],
                        input_hash,
                        Jsonb(input_payload),
                        parameter_hash,
                        command_payload["seed"],
                        Jsonb(list(preview.assumptions)),
                        command_payload["idempotencyKey"],
                        command_payload["requestId"],
                        command_payload["actorId"],
                        requested_at_dt,
                        command_payload["reason"],
                        request_hash,
                        command_payload["maxAttempts"],
                    ),
                ).fetchone()
                assert run is not None
                return _run_summary_response(run)
        except errors.SerializationFailure:
            if attempt == 2:
                raise
    raise RuntimeError("백테스트 실행 생성 재시도 한도를 초과했다.")


def persist_completed_run(
    repository: object,
    *,
    request_id: object,
    idempotency_key: object,
    actor_id: object,
    requested_at: object,
    reason: object,
    spec: object,
    result: object,
    started_at: object | None = None,
    completed_at: object | None = None,
    artifacts: object = (),
) -> Row:
    if not isinstance(spec, BacktestEngineSpec):
        raise ValueError("백테스트 spec이 필요하다.")
    if not isinstance(result, BacktestResult):
        raise ValueError("백테스트 result가 필요하다.")
    artifact_rows = _artifact_inputs(artifacts)
    requested_at_dt = _datetime(requested_at, "requestedAt")
    started_at_dt = (
        _datetime(started_at, "startedAt") if started_at is not None else requested_at_dt
    )
    completed_at_dt = (
        _datetime(completed_at, "completedAt") if completed_at is not None else requested_at_dt
    )
    payload = {
        "requestId": _non_blank(request_id, "requestId"),
        "idempotencyKey": _non_blank(idempotency_key, "idempotencyKey"),
        "actorId": _non_blank(actor_id, "actorId"),
        "requestedAt": requested_at_dt.isoformat(),
        "reason": _non_blank(reason, "reason"),
        "inputHash": result.input_hash,
        "resultHash": result.result_hash,
    }
    request_hash = _hash(payload)
    connector = _connector(repository)
    for attempt in range(3):
        try:
            with connector() as connection:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"backtest-run:{payload['idempotencyKey']}",),
                )
                existing = connection.execute(
                    "SELECT id, request_hash FROM backtest_runs WHERE idempotency_key=%s",
                    (payload["idempotencyKey"],),
                ).fetchone()
                if existing is not None:
                    if existing["request_hash"] != request_hash:
                        raise BacktestIdempotencyConflictError(
                            "멱등 키의 기존 백테스트 저장 요청과 본문이 다르다."
                        )
                    replay = get_run(repository, int(cast(int, existing["id"])))
                    assert replay is not None
                    return replay

                run = connection.execute(
                    """
                    INSERT INTO backtest_runs (
                      strategy_version_id, strategy_graph_hash,
                      dataset_version_id, dataset_content_hash,
                      engine_version, status, input_hash, result_hash,
                      parameter_hash, seed, assumptions, idempotency_key,
                      request_id, actor_id, requested_at, reason, request_hash,
                      started_at, finished_at
                    ) VALUES (
                      %s,%s,%s,%s,%s,'succeeded',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                    ) RETURNING *
                    """,
                    (
                        spec.strategy_version_id,
                        spec.strategy_graph_hash,
                        spec.dataset_version_id,
                        spec.dataset_content_hash,
                        spec.engine_version,
                        result.input_hash,
                        result.result_hash,
                        spec.parameter_hash,
                        spec.seed,
                        Jsonb(list(result.assumptions)),
                        payload["idempotencyKey"],
                        payload["requestId"],
                        payload["actorId"],
                        requested_at_dt,
                        payload["reason"],
                        request_hash,
                        started_at_dt,
                        completed_at_dt,
                    ),
                ).fetchone()
                assert run is not None
                run_id = int(cast(int, run["id"]))
                _insert_trades(connection, run_id, result)
                _insert_equity_points(connection, run_id, result)
                _insert_metrics(connection, run_id, result)
                _insert_artifacts(connection, run_id, artifact_rows)
                saved = _get_run_with_connection(connection, run_id)
                assert saved is not None
                return saved
        except errors.SerializationFailure:
            if attempt == 2:
                raise
    raise RuntimeError("백테스트 저장 재시도 한도를 초과했다.")


def get_run(repository: object, backtest_run_id: int) -> Row | None:
    with _connector(repository)() as connection:
        return _get_run_with_connection(connection, backtest_run_id)


def list_runs(repository: object, *, page_size: object, cursor: object) -> Row:
    if not isinstance(page_size, int) or page_size < 1:
        raise ValueError("pageSize는 1 이상 정수여야 한다.")
    with _connector(repository)() as connection:
        if cursor is None:
            ceiling_row = connection.execute(
                "SELECT COALESCE(MAX(id), 0) AS id FROM backtest_runs"
            ).fetchone()
            ceiling = int(cast(int, ceiling_row["id"])) if ceiling_row is not None else 0
            last_id = ceiling + 1
        else:
            ceiling, last_id = _decode_list_cursor(cursor)
        rows = connection.execute(
            """
            SELECT
              id, strategy_version_id, dataset_version_id, engine_version, status,
              input_hash, result_hash, requested_at, started_at, finished_at
            FROM backtest_runs
            WHERE id <= %s AND id < %s
            ORDER BY id DESC
            LIMIT %s
            """,
            (ceiling, last_id, page_size + 1),
        ).fetchall()
    page = rows[:page_size]
    return {
        "items": [_run_summary_response(row) for row in page],
        "nextCursor": (
            _encode_list_cursor(ceiling=ceiling, last_id=int(cast(int, page[-1]["id"])))
            if len(rows) > page_size
            else None
        ),
    }


def list_run_trades(
    repository: object,
    *,
    backtest_run_id: object,
    page_size: object,
    cursor: object,
) -> Row | None:
    run_id = _positive_int(backtest_run_id, "backtestRunId")
    page_size_int = _positive_int(page_size, "pageSize")
    return _list_run_child_rows(
        repository,
        backtest_run_id=run_id,
        page_size=page_size_int,
        cursor=cursor,
        cursor_kind=_TRADE_CURSOR_KIND,
        table_name="backtest_trades",
        sequence_column="trade_sequence",
        row_mapper=_trade_response,
    )


def list_run_equity_points(
    repository: object,
    *,
    backtest_run_id: object,
    page_size: object,
    cursor: object,
) -> Row | None:
    run_id = _positive_int(backtest_run_id, "backtestRunId")
    page_size_int = _positive_int(page_size, "pageSize")
    return _list_run_child_rows(
        repository,
        backtest_run_id=run_id,
        page_size=page_size_int,
        cursor=cursor,
        cursor_kind=_EQUITY_CURSOR_KIND,
        table_name="backtest_equity_points",
        sequence_column="point_sequence",
        row_mapper=_equity_point_response,
    )


def _list_run_child_rows(
    repository: object,
    *,
    backtest_run_id: int,
    page_size: int,
    cursor: object,
    cursor_kind: str,
    table_name: str,
    sequence_column: str,
    row_mapper: Callable[[Mapping[str, object]], Row],
) -> Row | None:
    if page_size < 1:
        raise ValueError("pageSize는 1 이상 정수여야 한다.")
    with _connector(repository)() as connection:
        exists = connection.execute(
            "SELECT 1 FROM backtest_runs WHERE id=%s",
            (backtest_run_id,),
        ).fetchone()
        if exists is None:
            return None
        if cursor is None:
            ceiling_row = connection.execute(
                f"SELECT COALESCE(MAX({sequence_column}), 0) AS sequence "
                f"FROM {table_name} WHERE run_id=%s",
                (backtest_run_id,),
            ).fetchone()
            ceiling = (
                int(cast(int, ceiling_row["sequence"])) if ceiling_row is not None else 0
            )
            last_sequence = 0
        else:
            cursor_run_id, ceiling, last_sequence = _decode_result_cursor(
                cursor,
                expected_kind=cursor_kind,
            )
            if cursor_run_id != backtest_run_id:
                raise BacktestCursorMismatchError(
                    "백테스트 결과 cursor가 현재 조회 문맥과 다릅니다."
                )
        rows = connection.execute(
            f"""
            SELECT * FROM {table_name}
            WHERE run_id=%s AND {sequence_column} <= %s AND {sequence_column} > %s
            ORDER BY {sequence_column}
            LIMIT %s
            """,
            (backtest_run_id, ceiling, last_sequence, page_size + 1),
        ).fetchall()
    page = rows[:page_size]
    next_cursor = None
    if len(rows) > page_size and page:
        next_cursor = _encode_result_cursor(
            kind=cursor_kind,
            backtest_run_id=backtest_run_id,
            ceiling=ceiling,
            last_sequence=int(cast(int, page[-1][sequence_column])),
        )
    return {
        "backtestRunId": backtest_run_id,
        "items": [row_mapper(row) for row in page],
        "nextCursor": next_cursor,
    }


def _get_run_with_connection(connection: Any, backtest_run_id: int) -> Row | None:
    run = connection.execute(
        "SELECT * FROM backtest_runs WHERE id=%s", (backtest_run_id,)
    ).fetchone()
    if run is None:
        return None
    trades = connection.execute(
        "SELECT * FROM backtest_trades WHERE run_id=%s ORDER BY trade_sequence",
        (backtest_run_id,),
    ).fetchall()
    metrics = connection.execute(
        """
        SELECT * FROM backtest_metrics
        WHERE run_id=%s ORDER BY metric_name, scope_key
        """,
        (backtest_run_id,),
    ).fetchall()
    artifacts = connection.execute(
        """
        SELECT * FROM backtest_artifacts
        WHERE run_id=%s ORDER BY artifact_type, id
        """,
        (backtest_run_id,),
    ).fetchall()
    return {
        "backtestRunId": int(cast(int, run["id"])),
        "strategyVersionId": int(cast(int, run["strategy_version_id"])),
        "datasetVersionId": int(cast(int, run["dataset_version_id"])),
        "inputHash": run["input_hash"],
        "resultHash": run["result_hash"],
        "status": _run_status(run["status"]),
        "metrics": [_metric_response(row) for row in metrics],
        "trades": [_trade_response(row) for row in trades],
        "artifacts": [_artifact_response(row) for row in artifacts],
        "request_hash": run["request_hash"],
    }


def _get_run_summary_with_connection(connection: Any, backtest_run_id: int) -> Row | None:
    row = connection.execute(
        """
        SELECT
          id, strategy_version_id, dataset_version_id, engine_version, status,
          input_hash, result_hash, requested_at, started_at, finished_at
        FROM backtest_runs WHERE id=%s
        """,
        (backtest_run_id,),
    ).fetchone()
    if row is None:
        return None
    return _run_summary_response(row)


def _load_candle_events(
    connection: Any, dataset_version_id: int
) -> tuple[BacktestCandleEvent, ...]:
    rows = connection.execute(
        """
        SELECT
          candle.instrument_id,
          instrument.market_code,
          candle.occurred_at,
          candle.knowledge_at,
          CASE
            WHEN candle.source_candle_revision_id IS NOT NULL
              THEN 'source:' || candle.source_candle_revision_id::text
            ELSE 'rollup:' || candle.candle_rollup_id::text
          END AS stable_sequence,
          COALESCE(revision.open_price, rollup.open_price) AS open_price,
          COALESCE(revision.high_price, rollup.high_price) AS high_price,
          COALESCE(revision.low_price, rollup.low_price) AS low_price,
          COALESCE(revision.close_price, rollup.close_price) AS close_price,
          COALESCE(revision.trade_volume, rollup.trade_volume) AS trade_volume,
          candle.quality,
          candle.content_hash
        FROM dataset_version_candles candle
        JOIN instruments instrument ON instrument.id=candle.instrument_id
        LEFT JOIN source_candle_revisions revision
          ON revision.id=candle.source_candle_revision_id
        LEFT JOIN candle_rollups rollup ON rollup.id=candle.candle_rollup_id
        WHERE candle.dataset_version_id=%s
        ORDER BY candle.knowledge_at, stable_sequence
        """,
        (dataset_version_id,),
    ).fetchall()
    return tuple(
        BacktestCandleEvent(
            instrument_id=int(cast(int, row["instrument_id"])),
            market_code=str(row["market_code"]),
            occurred_at=cast(datetime, row["occurred_at"]),
            knowledge_at=cast(datetime, row["knowledge_at"]),
            stable_sequence=str(row["stable_sequence"]),
            open=cast(Decimal, row["open_price"]),
            high=cast(Decimal, row["high_price"]),
            low=cast(Decimal, row["low_price"]),
            close=cast(Decimal, row["close_price"]),
            volume=cast(Decimal, row["trade_volume"]),
            quality=cast(Any, row["quality"]),
            content_hash=str(row["content_hash"]),
        )
        for row in rows
    )


def _engine_spec_payload(spec: BacktestEngineSpec) -> Row:
    return {
        "datasetVersionId": spec.dataset_version_id,
        "datasetContentHash": spec.dataset_content_hash,
        "strategyVersionId": spec.strategy_version_id,
        "strategyGraphHash": spec.strategy_graph_hash,
        "engineVersion": spec.engine_version,
        "parameterHash": spec.parameter_hash,
        "seed": spec.seed,
        "initialCash": str(spec.initial_cash),
        "execution": {
            "fee_rate": str(spec.execution.fee_rate),
            "slippage_bps": str(spec.execution.slippage_bps),
            "latency_seconds": spec.execution.latency_seconds,
            "max_participation_rate": str(spec.execution.max_participation_rate),
        },
    }


def _engine_event_payload(event: BacktestCandleEvent) -> Row:
    return {
        "instrumentId": event.instrument_id,
        "marketCode": event.market_code,
        "occurredAt": _json_time(event.occurred_at),
        "knowledgeAt": _json_time(event.knowledge_at),
        "stableSequence": event.stable_sequence,
        "sourcePriority": event.source_priority,
        "open": str(event.open),
        "high": str(event.high),
        "low": str(event.low),
        "close": str(event.close),
        "volume": str(event.volume),
        "quality": event.quality,
        "contentHash": event.content_hash,
    }


def _insert_trades(connection: Any, run_id: int, result: BacktestResult) -> None:
    for index, trade in enumerate(result.trades, start=1):
        connection.execute(
            """
            INSERT INTO backtest_trades (
              run_id, trade_sequence, signal_sequence, side, requested_quantity,
              filled_quantity, remaining_quantity, fill_price, fee_paid, status,
              occurred_at, knowledge_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                run_id,
                index,
                index,
                trade.side,
                trade.requested_quantity,
                trade.filled_quantity,
                trade.remaining_quantity,
                trade.fill_price,
                trade.fee_paid,
                trade.status,
                trade.occurred_at,
                trade.knowledge_at,
            ),
        )


def _insert_equity_points(connection: Any, run_id: int, result: BacktestResult) -> None:
    for index, point in enumerate(result.equity_points, start=1):
        connection.execute(
            """
            INSERT INTO backtest_equity_points (
              run_id, point_sequence, occurred_at, knowledge_at,
              cash, base_position, equity
            ) VALUES (%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                run_id,
                index,
                point.occurred_at,
                point.knowledge_at,
                point.cash,
                point.base_position,
                point.equity,
            ),
        )


def _insert_metrics(connection: Any, run_id: int, result: BacktestResult) -> None:
    for name, value in sorted(result.metrics.items()):
        connection.execute(
            """
            INSERT INTO backtest_metrics (run_id, metric_name, scope_key, metric_value)
            VALUES (%s,%s,'run',%s)
            """,
            (run_id, name, value),
        )


def _insert_artifacts(
    connection: Any,
    run_id: int,
    artifacts: Sequence[Mapping[str, object]],
) -> None:
    for artifact in artifacts:
        connection.execute(
            """
            INSERT INTO backtest_artifacts (
              run_id, artifact_type, content_hash, media_type, storage_uri, artifact_json
            ) VALUES (%s,%s,%s,'application/json',%s,%s)
            """,
            (
                run_id,
                artifact["artifactType"],
                artifact["contentHash"],
                artifact.get("storageUri"),
                Jsonb(artifact.get("metadata", {})),
            ),
        )


def _metric_response(row: Mapping[str, object]) -> Row:
    return {
        "metricName": row["metric_name"],
        "scopeKey": row["scope_key"],
        "metricValue": cast(Decimal | None, row["metric_value"]),
        "metricPayload": row["metric_payload"],
    }


def _trade_response(row: Mapping[str, object]) -> Row:
    return {
        "tradeSequence": row["trade_sequence"],
        "side": row["side"],
        "requestedQuantity": cast(Decimal, row["requested_quantity"]),
        "filledQuantity": cast(Decimal, row["filled_quantity"]),
        "remainingQuantity": cast(Decimal, row["remaining_quantity"]),
        "fillPrice": cast(Decimal, row["fill_price"]),
        "feePaid": cast(Decimal, row["fee_paid"]),
        "status": row["status"],
        "occurredAt": row["occurred_at"],
        "knowledgeAt": row["knowledge_at"],
    }


def _equity_point_response(row: Mapping[str, object]) -> Row:
    return {
        "pointSequence": row["point_sequence"],
        "occurredAt": row["occurred_at"],
        "knowledgeAt": row["knowledge_at"],
        "cash": cast(Decimal, row["cash"]),
        "basePosition": cast(Decimal, row["base_position"]),
        "equity": cast(Decimal, row["equity"]),
    }


def _artifact_response(row: Mapping[str, object]) -> Row:
    return {
        "artifactType": row["artifact_type"],
        "contentHash": row["content_hash"],
        "mediaType": row["media_type"],
        "storageUri": row["storage_uri"],
        "metadata": row["artifact_json"],
    }


def _run_summary_response(row: Mapping[str, object]) -> Row:
    return {
        "backtestRunId": int(cast(int, row["id"])),
        "strategyVersionId": int(cast(int, row["strategy_version_id"])),
        "datasetVersionId": int(cast(int, row["dataset_version_id"])),
        "engineVersion": row["engine_version"],
        "status": _run_status(row["status"]),
        "inputHash": row["input_hash"],
        "resultHash": row["result_hash"],
        "requestedAt": row["requested_at"],
        "startedAt": row["started_at"],
        "finishedAt": row["finished_at"],
    }


def _claimed_run_response(row: Mapping[str, object]) -> Row:
    return {
        "backtestRunId": int(cast(int, row["id"])),
        "strategyVersionId": int(cast(int, row["strategy_version_id"])),
        "strategyGraphHash": row["strategy_graph_hash"],
        "datasetVersionId": int(cast(int, row["dataset_version_id"])),
        "datasetContentHash": row["dataset_content_hash"],
        "engineVersion": row["engine_version"],
        "status": row["status"],
        "inputHash": row["input_hash"],
        "inputPayload": row["input_payload"],
        "parameterHash": row["parameter_hash"],
        "seed": int(cast(int, row["seed"])),
        "leaseOwner": row["lease_owner"],
        "leaseGeneration": int(cast(int, row["lease_generation"])),
        "attemptCount": int(cast(int, row["attempt_count"])),
        "maxAttempts": int(cast(int, row["max_attempts"])),
        "nextRetryAt": row["next_retry_at"],
        "lastErrorCode": row["last_error_code"],
        "lastErrorMessage": row["last_error_message"],
        "deadLetterReason": row["dead_letter_reason"],
    }


def _run_status(value: object) -> object:
    if value in {"queued", "retry_wait"}:
        return "pending"
    if value == "dead_letter":
        return "failed"
    return value


def _artifact_inputs(value: object) -> tuple[Mapping[str, object], ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, str):
        raise ValueError("백테스트 artifact 목록은 배열이어야 한다.")
    rows: list[Mapping[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("백테스트 artifact는 객체여야 한다.")
        rows.append(item)
    return tuple(rows)


def _connector(repository: object) -> Callable[[], Any]:
    connector = getattr(repository, "_connect", None)
    if connector is None:
        raise RuntimeError("백테스트 저장소는 PostgreSQL 연결이 필요하다.")
    return cast(Callable[[], Any], connector)


def _non_blank(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field}는 공백일 수 없다.")
    return value.strip()


def _datetime(value: object, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{field}는 UTC datetime이어야 한다.")
    return value


def _parameter_payload(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("parameters는 객체여야 한다.")
    result: dict[str, object] = {}
    for key, parameter_value in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("parameters key는 공백이 아닌 문자열이어야 한다.")
        if parameter_value is not None and not isinstance(parameter_value, str | int | bool):
            raise ValueError(
                "parameters value는 string, integer, boolean, null만 허용한다."
            )
        result[key.strip()] = parameter_value
    return result


def _seed(value: object) -> int:
    if type(value) is not int:
        raise ValueError("seed는 정수여야 한다.")
    return value


def _json_time(value: object) -> str:
    if not isinstance(value, datetime):
        raise ValueError("백테스트 입력 materialization 시각이 datetime이 아니다.")
    return value.isoformat().replace("+00:00", "Z")


def _decimal_text(value: object, field: str) -> str:
    if not isinstance(value, Decimal):
        raise ValueError(f"{field}는 Decimal이어야 한다.")
    return str(value)


def _execution_payload(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("execution은 객체여야 한다.")
    fee_rate = value.get("feeRate")
    slippage_bps = value.get("slippageBps")
    latency_seconds = value.get("latencySeconds")
    max_participation_rate = value.get("maxParticipationRate")
    if not all(
        isinstance(item, Decimal)
        for item in (fee_rate, slippage_bps, max_participation_rate)
    ):
        raise ValueError("execution feeRate, slippageBps, maxParticipationRate는 Decimal이다.")
    if type(latency_seconds) is not int or latency_seconds < 0:
        raise ValueError("execution latencySeconds는 0 이상 정수다.")
    return {
        "feeRate": str(fee_rate),
        "slippageBps": str(slippage_bps),
        "latencySeconds": latency_seconds,
        "maxParticipationRate": str(max_participation_rate),
    }


def _hash(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _encode_list_cursor(*, ceiling: int, last_id: int) -> str:
    payload = {"ceiling": ceiling, "lastId": last_id}
    envelope = {
        "kind": _LIST_CURSOR_KIND,
        "payload": payload,
        "digest": _cursor_digest(payload),
    }
    encoded = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
    return urlsafe_b64encode(encoded).decode().rstrip("=")


def _decode_list_cursor(value: object) -> tuple[int, int]:
    if not isinstance(value, str) or not value.strip():
        raise BacktestCursorMismatchError("유효하지 않은 백테스트 run 목록 cursor다.")
    try:
        padded = value + "=" * (-len(value) % 4)
        envelope = json.loads(urlsafe_b64decode(padded.encode()).decode())
        if not isinstance(envelope, Mapping):
            raise BacktestCursorMismatchError("유효하지 않은 백테스트 run 목록 cursor 구조다.")
        if envelope.get("kind") != _LIST_CURSOR_KIND:
            raise BacktestCursorMismatchError(
                "백테스트 run 목록 cursor가 현재 조회 문맥과 다릅니다."
            )
        payload = envelope.get("payload")
        if not isinstance(payload, Mapping):
            raise BacktestCursorMismatchError("유효하지 않은 백테스트 run 목록 cursor 구조다.")
        digest = envelope.get("digest")
        if not isinstance(digest, str) or not hmac.compare_digest(
            digest, _cursor_digest(payload)
        ):
            raise BacktestCursorMismatchError("백테스트 run 목록 cursor 무결성 검증에 실패했다.")
        ceiling = payload.get("ceiling")
        last_id = payload.get("lastId")
        if type(ceiling) is not int or type(last_id) is not int:
            raise BacktestCursorMismatchError("유효하지 않은 백테스트 run 목록 cursor 구조다.")
        if ceiling < 0 or last_id < 1 or last_id > ceiling + 1:
            raise BacktestCursorMismatchError("유효하지 않은 백테스트 run 목록 cursor 범위다.")
        return ceiling, last_id
    except BacktestCursorMismatchError:
        raise
    except Exception as exc:
        raise BacktestCursorMismatchError(
            "유효하지 않은 백테스트 run 목록 cursor다."
        ) from exc


def _cursor_digest(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hmac.new(
        _CURSOR_HMAC_SECRET.encode(),
        encoded.encode(),
        hashlib.sha256,
    ).hexdigest()


def _encode_result_cursor(
    *,
    kind: str,
    backtest_run_id: int,
    ceiling: int,
    last_sequence: int,
) -> str:
    payload = {
        "backtestRunId": backtest_run_id,
        "ceiling": ceiling,
        "lastSequence": last_sequence,
    }
    envelope = {
        "kind": kind,
        "payload": payload,
        "digest": _cursor_digest(payload),
    }
    encoded = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
    return urlsafe_b64encode(encoded).decode().rstrip("=")


def _decode_result_cursor(
    value: object,
    *,
    expected_kind: str,
) -> tuple[int, int, int]:
    if not isinstance(value, str) or not value.strip():
        raise BacktestCursorMismatchError("유효하지 않은 백테스트 결과 cursor다.")
    try:
        padded = value + "=" * (-len(value) % 4)
        envelope = json.loads(urlsafe_b64decode(padded.encode()).decode())
        if not isinstance(envelope, Mapping):
            raise BacktestCursorMismatchError("유효하지 않은 백테스트 결과 cursor 구조다.")
        if envelope.get("kind") != expected_kind:
            raise BacktestCursorMismatchError(
                "백테스트 결과 cursor가 현재 조회 문맥과 다릅니다."
            )
        payload = envelope.get("payload")
        if not isinstance(payload, Mapping):
            raise BacktestCursorMismatchError("유효하지 않은 백테스트 결과 cursor 구조다.")
        digest = envelope.get("digest")
        if not isinstance(digest, str) or not hmac.compare_digest(
            digest, _cursor_digest(payload)
        ):
            raise BacktestCursorMismatchError("백테스트 결과 cursor 무결성 검증에 실패했다.")
        run_id = payload.get("backtestRunId")
        ceiling = payload.get("ceiling")
        last_sequence = payload.get("lastSequence")
        if type(run_id) is not int or type(ceiling) is not int or type(last_sequence) is not int:
            raise BacktestCursorMismatchError("유효하지 않은 백테스트 결과 cursor 구조다.")
        if run_id < 1 or ceiling < 0 or last_sequence < 0 or last_sequence > ceiling:
            raise BacktestCursorMismatchError("유효하지 않은 백테스트 결과 cursor 범위다.")
        return run_id, ceiling, last_sequence
    except BacktestCursorMismatchError:
        raise
    except Exception as exc:
        raise BacktestCursorMismatchError("유효하지 않은 백테스트 결과 cursor다.") from exc


def _positive_int(value: object, field: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{field}는 1 이상 정수여야 한다.")
    return value
