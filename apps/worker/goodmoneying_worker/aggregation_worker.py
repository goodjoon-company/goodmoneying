from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable

from goodmoneying_shared.dataset_version_store import (
    publish_next_build as publish_next_dataset_build,
)
from goodmoneying_shared.indicator_store import run_next_indicator_invalidation
from goodmoneying_shared.microstructure_store import run_next_microstructure_invalidation
from goodmoneying_shared.models import CollectionWorkerHeartbeatStatus
from goodmoneying_shared.repository import OperationsRepository

HEARTBEAT_INTERVAL_SECONDS = 5.0
HEARTBEAT_SHUTDOWN_GRACE_SECONDS = 3.0
HEARTBEAT_THREAD_NAME = "candle-aggregation-heartbeat"
logger = logging.getLogger(__name__)


class PeriodicHeartbeatRunner:
    def __init__(
        self,
        heartbeat: Callable[[], None],
        interval_seconds: float,
        shutdown_grace_seconds: float = HEARTBEAT_SHUTDOWN_GRACE_SECONDS,
    ) -> None:
        self._heartbeat = heartbeat
        self._interval_seconds = interval_seconds
        self._shutdown_grace_seconds = shutdown_grace_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> PeriodicHeartbeatRunner:
        self._thread = threading.Thread(
            target=self._run,
            name=HEARTBEAT_THREAD_NAME,
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._shutdown_grace_seconds)
            if self._thread.is_alive():
                logger.error(
                    "aggregation_heartbeat_shutdown_timeout grace_seconds=%s",
                    self._shutdown_grace_seconds,
                )

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._heartbeat()
            except Exception:
                logger.exception("aggregation_heartbeat_failed")
            if self._stop_event.wait(self._interval_seconds):
                return


class CandleAggregationWorker:
    def __init__(
        self,
        repository: OperationsRepository,
        heartbeat_repository: OperationsRepository | None = None,
        worker_id: str | None = None,
    ) -> None:
        self._repository = repository
        self._heartbeat_repository = heartbeat_repository or repository
        self._heartbeat_single_flight = threading.Lock()
        self._worker_id = worker_id or f"candle-aggregation-{uuid.uuid4()}"

    def run_once(self) -> int:
        claim_incremental = getattr(
            self._repository, "claim_next_candle_rollup_recompute_job", None
        )
        if claim_incremental is not None:
            incremental_job = claim_incremental(self._worker_id)
            if incremental_job is not None:
                try:
                    return int(
                        self._repository.run_candle_rollup_recompute_job(
                            incremental_job.id, self._worker_id
                        )
                    )
                except Exception as exc:
                    self._repository.fail_candle_rollup_recompute_job(
                        incremental_job.id, self._worker_id, type(exc).__name__
                    )
                    raise
        self._repository.schedule_candle_aggregation()
        job = self._repository.claim_next_candle_aggregation_job()
        if job is None:
            indicator_rows = run_next_indicator_invalidation(self._repository, self._worker_id)
            if indicator_rows > 0:
                return indicator_rows
            microstructure_rows = run_next_microstructure_invalidation(
                self._repository, self._worker_id
            )
            if microstructure_rows > 0:
                return microstructure_rows
            return publish_next_dataset_build(self._repository, self._worker_id)
        completed = 0
        for target in self._repository.candle_aggregation_job_targets(job.id):
            self._repository.mark_candle_aggregation_target(
                job.id,
                target.instrument_id,
                target.candle_unit,
                "running",
                target.rows_written,
            )
            try:
                rows_written = self._repository.materialize_candle_rollups(
                    target.instrument_id,
                    target.candle_unit,
                )
            except Exception:
                self._repository.mark_candle_aggregation_target(
                    job.id,
                    target.instrument_id,
                    target.candle_unit,
                    "failed",
                    target.rows_written,
                )
                raise
            self._repository.mark_candle_aggregation_target(
                job.id,
                target.instrument_id,
                target.candle_unit,
                "succeeded",
                rows_written,
            )
            completed += 1
        return completed

    def heartbeat_lifecycle(self) -> PeriodicHeartbeatRunner:
        return PeriodicHeartbeatRunner(
            self._record_running_heartbeat,
            HEARTBEAT_INTERVAL_SECONDS,
        )

    def _record_running_heartbeat(self) -> None:
        if not self.record_heartbeat("running"):
            logger.error("aggregation_running_heartbeat_skipped reason=in_flight")

    def record_heartbeat(
        self,
        status: CollectionWorkerHeartbeatStatus,
        error_message: str | None = None,
    ) -> bool:
        if not self._heartbeat_single_flight.acquire(blocking=False):
            return False
        try:
            self._heartbeat_repository.record_collection_worker_heartbeat(
                "candle_aggregation", status, error_message
            )
        finally:
            self._heartbeat_single_flight.release()
        return True
