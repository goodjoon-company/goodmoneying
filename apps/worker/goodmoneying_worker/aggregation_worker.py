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
        completed = 0
        for target in self._repository.candle_aggregation_job_targets(job.id):
            self._repository.mark_candle_aggregation_target(
                job.id, target.instrument_id, target.candle_unit, "running", target.rows_written
            )
            try:
                rows_written = self._repository.materialize_candle_rollups(
                    target.instrument_id, target.candle_unit
                )
            except Exception:
                self._repository.mark_candle_aggregation_target(
                    job.id, target.instrument_id, target.candle_unit, "failed", target.rows_written
                )
                raise
            self._repository.mark_candle_aggregation_target(
                job.id, target.instrument_id, target.candle_unit, "succeeded", rows_written
            )
            completed += 1
        return completed
