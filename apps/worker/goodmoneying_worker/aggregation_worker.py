from __future__ import annotations

from goodmoneying_shared.repository import OperationsRepository


class CandleAggregationWorker:
    def __init__(self, repository: OperationsRepository) -> None:
        self._repository = repository

    def run_once(self) -> int:
        self._repository.schedule_candle_aggregation()
        job = self._repository.claim_next_candle_aggregation_job()
        if job is None:
            return 0
        self._record_running_heartbeat()
        completed = 0
        for target in self._repository.candle_aggregation_job_targets(job.id):
            self._record_running_heartbeat()
            self._repository.mark_candle_aggregation_target(
                job.id, target.instrument_id, target.candle_unit, "running", target.rows_written
            )
            try:
                rows_written = self._repository.materialize_candle_rollups(
                    target.instrument_id,
                    target.candle_unit,
                    self._record_running_heartbeat,
                )
            except Exception:
                self._repository.mark_candle_aggregation_target(
                    job.id, target.instrument_id, target.candle_unit, "failed", target.rows_written
                )
                raise
            self._repository.mark_candle_aggregation_target(
                job.id, target.instrument_id, target.candle_unit, "succeeded", rows_written
            )
            self._record_running_heartbeat()
            completed += 1
        return completed

    def _record_running_heartbeat(self) -> None:
        self._repository.record_collection_worker_heartbeat(
            "candle_aggregation", "running"
        )
