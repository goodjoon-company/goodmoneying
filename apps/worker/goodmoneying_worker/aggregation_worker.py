from __future__ import annotations

import logging
import threading
from collections.abc import Callable

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
    ) -> None:
        self._repository = repository
        self._heartbeat_repository = heartbeat_repository or repository

    def run_once(self) -> int:
        self._repository.schedule_candle_aggregation()
        job = self._repository.claim_next_candle_aggregation_job()
        if job is None:
            return 0
        with PeriodicHeartbeatRunner(
            self._record_running_heartbeat, HEARTBEAT_INTERVAL_SECONDS
        ):
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

    def _record_running_heartbeat(self) -> None:
        self.record_heartbeat("running")

    def record_heartbeat(
        self,
        status: CollectionWorkerHeartbeatStatus,
        error_message: str | None = None,
    ) -> None:
        self._heartbeat_repository.record_collection_worker_heartbeat(
            "candle_aggregation", status, error_message
        )
