from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from time import perf_counter

import pytest
from fastapi.testclient import TestClient

from goodmoneying_api.main import create_app
from goodmoneying_shared.models import SourceCandle
from goodmoneying_shared.sqlite_repository import SQLiteOperationsRepository
from goodmoneying_shared.time import now_kst
from goodmoneying_worker.collector import seed_repository
from goodmoneying_worker.upbit_client import FixtureUpbitClient


def seeded_repository_and_client() -> tuple[SQLiteOperationsRepository, TestClient]:
    repository = SQLiteOperationsRepository()
    seed_repository(repository, FixtureUpbitClient())
    return repository, TestClient(create_app(repository))


def seeded_client() -> TestClient:
    return seeded_repository_and_client()[1]


def without_relative_freshness(items: list[dict[str, object]]) -> list[dict[str, object]]:
    normalized = []
    for item in items:
        assert str(item["tickerFreshnessLabel"]).endswith("전")
        normalized.append({**item, "tickerFreshnessLabel": "<relative>"})
    return normalized


def test_default_api_repository_does_not_auto_seed_fixture_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GOODMONEYING_DATABASE_URL", raising=False)
    monkeypatch.delenv("GOODMONEYING_DEMO_DATA", raising=False)

    client = TestClient(create_app())

    response = client.get("/v1/dashboard/summary")

    assert response.status_code == 200
    assert response.json()["totals"]["activeTargets"] == 0
    assert response.json()["targets"] == []


def test_dashboard_candidate_market_and_detail_endpoints() -> None:
    client = seeded_client()

    dashboard = client.get("/v1/dashboard/summary")
    universe = client.get("/v1/candidate-universe")
    market_list = client.get("/v1/market-list")

    assert dashboard.status_code == 200
    assert dashboard.json()["totals"]["activeTargets"] == 50
    assert len(dashboard.json()["coverage"]) == 150
    assert len(dashboard.json()["targets"]) == 50
    first_target = dashboard.json()["targets"][0]
    totals = dashboard.json()["totals"]
    metric_principles = dashboard.json()["metricPrinciples"]
    assert first_target["instrument"]["marketCode"] == "KRW-BTC"
    assert first_target["overallStatus"] == "warning"
    assert first_target["overallStatusLabel"] == "주의"
    assert first_target["plan"]["isContinuous"] is True
    assert first_target["plan"]["rangeTimeZone"] == "KST"
    assert first_target["coverageSegments"] == []
    assert first_target["accTradePrice24hDisplay"].startswith("₩")
    assert first_target["changeRate"]
    assert first_target["tickerFreshnessLabel"].endswith("전")
    assert first_target["coveragePercent"]
    assert first_target["storageBytesDisplay"].endswith(("KB", "MB", "GB"))
    assert totals["activeTargetLimit"] == 50
    assert totals["normalTargets"] + totals["warningTargets"] + totals["incidentTargets"] == 50
    assert totals["storageBytesToday"] > 0
    assert totals["storageBytesTodayDisplay"].endswith(("MB", "GB"))
    assert totals["storageRowsToday"] > 0
    assert totals["realtimeRowsLastMinute"] >= 0
    assert totals["backfillRowsLastMinute"] >= 0
    assert "failureRate24h" in totals
    assert "rateLimitRemainingPercent" not in totals
    assert "duplicateRows24h" not in totals
    assert {
        (principle["metricKey"], principle["displayStatus"])
        for principle in metric_principles
    } >= {
        ("rateLimitRemainingPercent", "excluded"),
        ("duplicateRows24h", "excluded"),
    }
    assert all(principle["reason"] for principle in metric_principles)
    assert len(dashboard.json()["collectionActivity"]) == 168
    assert {
        item["dataType"] for item in dashboard.json()["storageBreakdown"]
    } == {
        "source_candle",
        "ticker_snapshot",
        "orderbook_summary",
    }
    assert sum(item["rowCount"] for item in dashboard.json()["storageBreakdown"]) == totals[
        "storageRowsToday"
    ]
    assert len(dashboard.json()["realtimeCollectionHeatmap"]) == 50
    assert dashboard.json()["workerStatus"]["realtime"]["status"] in {
        "running",
        "stale",
        "failed",
    }
    assert dashboard.json()["workerStatus"]["backfill"]["runningTargetCount"] >= 0
    first_realtime_row = dashboard.json()["realtimeCollectionHeatmap"][0]
    assert first_realtime_row["instrument"]["id"] > 0
    assert len(first_realtime_row["hourlyBuckets"]) == 24
    assert {
        bucket["status"] for bucket in first_realtime_row["hourlyBuckets"]
    }.issubset({"red", "orange", "yellow", "blue", "green"})
    assert {
        "tradeCount",
        "averageTradesPerMinute",
        "tradeStrength",
        "tradeVolume",
        "tradeAmount",
        "status",
    }.issubset(first_realtime_row["hourlyBuckets"][0])
    assert len(dashboard.json()["operationsTrend"]) == 7
    assert dashboard.json()["missingRangeTop"][0]["missingSegmentCount"] >= 0
    assert dashboard.json()["auditLogSummary"]["targetChangeCount24h"] >= 50
    assert dashboard.json()["auditLogSummary"]["latestChangeAt"]
    assert dashboard.json()["healthChecks"][0]["title"]
    assert universe.status_code == 200
    assert len(universe.json()["entries"]) == 100
    assert universe.json()["entries"][0]["accTradePrice24hDisplay"].startswith("₩")
    assert "," in universe.json()["entries"][0]["accTradePrice24hDisplay"]
    assert universe.json()["entries"][0]["qualityStatus"] in {"normal", "warning", "incident"}
    assert universe.json()["entries"][0]["qualityDetail"]
    first_universe_entry = universe.json()["entries"][0]
    assert first_universe_entry["collectionRangeDisplay"].startswith("2026-01-01")
    assert first_universe_entry["collectedStartAt"]
    assert first_universe_entry["collectedEndAt"]
    assert first_universe_entry["collectedStartAt"] <= first_universe_entry["collectedEndAt"]
    assert first_universe_entry["isRealtimeTarget"] is True
    assert market_list.status_code == 200
    assert len(market_list.json()["rows"]) == 50
    first_market_row = market_list.json()["rows"][0]
    assert first_market_row["accTradePrice24hDisplay"].startswith("₩")
    assert "," in first_market_row["accTradePrice24hDisplay"]
    assert first_market_row["coveragePercent"]
    assert first_market_row["storageBytesDisplay"].endswith(("MB", "GB"))

    instrument_id = market_list.json()["rows"][0]["instrument"]["id"]
    detail = client.get(f"/v1/instruments/{instrument_id}")
    assert detail.status_code == 200
    assert detail.json()["latestTicker"]["tradePrice"]
    assert detail.json()["latestOrderbook"]["spread"]
    assert detail.json()["priceChangeAmount24h"]
    assert detail.json()["priceChangeRate24h"]
    assert detail.json()["tradeVolume24h"]
    assert detail.json()["tradeVolumeChangeRate24h"]
    assert detail.json()["tickerFreshnessLabel"].endswith("전")
    assert detail.json()["orderbookFreshnessLabel"].endswith("전")
    assert detail.json()["qualityHistory"][0]["status"] in {"normal", "warning", "incident"}
    assert detail.json()["qualityHistory"][0]["title"]


def test_dashboard_summary_stream_emits_dashboard_sse_event() -> None:
    client = seeded_client()

    with client.stream("GET", "/v1/dashboard/summary/stream?once=true") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        lines = response.iter_lines()

        assert next(lines) == "event: dashboard"
        data_line = next(lines)

    assert data_line.startswith("data: ")
    payload = json.loads(data_line.removeprefix("data: "))
    assert payload["status"] in {"normal", "warning", "incident"}
    assert payload["realtimeCollectionHeatmap"]


def test_dashboard_summary_exposes_collection_worker_status() -> None:
    repository, client = seeded_repository_and_client()
    instruments = repository.list_active_targets()
    started_at = now_kst() - timedelta(minutes=2)
    repository.record_collection_worker_heartbeat("realtime_collection", "running")
    repository.record_collection_worker_heartbeat("backfill_collection", "running")
    repository.record_collection_run_failure(
        "incremental",
        "ticker_snapshot",
        started_at,
        "UpbitTimeout",
        "현재가 수집 요청 시간이 초과되었습니다.",
    )
    failed_plan = repository.create_backfill_plan(
        "source_candle",
        now_kst() - timedelta(hours=4),
        now_kst() - timedelta(hours=3),
        [instruments[2].id],
    )
    failed_job = repository.approve_backfill_job(failed_plan.plan_id)
    repository.claim_next_backfill_job()
    repository.mark_backfill_target(
        failed_job.id,
        instruments[2].id,
        "failed",
        None,
        "UpbitBackfillError",
        "백필 캔들 조회 실패",
    )
    plan = repository.create_backfill_plan(
        "source_candle",
        now_kst() - timedelta(hours=2),
        now_kst() - timedelta(hours=1),
        [item.id for item in instruments[:2]],
    )
    job = repository.approve_backfill_job(plan.plan_id)
    repository.claim_next_backfill_job()
    repository.mark_backfill_target(
        job.id,
        instruments[0].id,
        "running",
        None,
    )
    repository.record_backfill_candles(
        job.id,
        instruments[1].id,
        [
            SourceCandle(
                instrument_id=instruments[1].id,
                candle_unit="1m",
                candle_start_at=now_kst() - timedelta(hours=2),
                open_price=Decimal("100"),
                high_price=Decimal("101"),
                low_price=Decimal("99"),
                close_price=Decimal("100"),
                trade_volume=Decimal("1"),
                trade_amount=Decimal("100"),
                collected_at=now_kst(),
            )
        ],
    )
    repository.mark_backfill_target(
        job.id,
        instruments[1].id,
        "succeeded",
        now_kst() - timedelta(hours=1),
    )
    queued_plan = repository.create_backfill_plan(
        "source_candle",
        now_kst() - timedelta(minutes=50),
        now_kst() - timedelta(minutes=10),
        [item.id for item in instruments[3:5]],
    )
    repository.approve_backfill_job(queued_plan.plan_id)

    response = client.get("/v1/dashboard/summary")

    assert response.status_code == 200
    worker_status = response.json()["workerStatus"]
    assert worker_status["realtime"]["status"] == "running"
    assert worker_status["realtime"]["lastHeartbeatAt"]
    assert worker_status["realtime"]["lastCollectedAt"]
    assert worker_status["realtime"]["collectedRowCount24h"] > 0
    assert worker_status["realtime"]["errorCount24h"] == 1
    assert worker_status["realtime"]["failureRate24h"] != "0"
    assert {
        "label": "마지막 heartbeat",
        "value": worker_status["realtime"]["lastHeartbeatAt"],
        "detail": "최근 heartbeat 정상",
    } in worker_status["realtime"]["diagnostics"]
    assert worker_status["realtime"]["recentErrors"][0]["code"] == "UpbitTimeout"
    assert worker_status["backfill"]["status"] == "running"
    assert worker_status["backfill"]["lastHeartbeatAt"]
    assert worker_status["backfill"]["lastCollectedAt"]
    assert worker_status["backfill"]["totalErrorCount"] == 1
    assert worker_status["backfill"]["failureRateAll"] != "0"
    assert worker_status["backfill"]["runningTargetCount"] == 1
    assert worker_status["backfill"]["totalTargetCount"] == 2
    assert worker_status["backfill"]["queuedJobCount"] == 1
    assert worker_status["backfill"]["queuedTargetCount"] == 2
    assert {
        "label": "동작중 코인",
        "value": "1/2개",
        "detail": "현재 실행 중인 백필 계획의 running 대상 수",
    } in worker_status["backfill"]["diagnostics"]
    assert {
        "label": "대기 백필",
        "value": "1건 / 2개",
        "detail": "현재 계획 이후 대기 중인 백필 job/target",
    } in worker_status["backfill"]["diagnostics"]
    assert worker_status["backfill"]["recentErrors"][0]["code"] == "UpbitBackfillError"


def test_backfill_jobs_expose_live_progress_and_hide_old_stopped_jobs() -> None:
    repository, client = seeded_repository_and_client()
    instruments = repository.list_active_targets()
    start_at = now_kst() - timedelta(minutes=5)
    end_at = now_kst()
    recent_stopped_plan = repository.create_backfill_plan(
        "source_candle",
        start_at,
        end_at,
        [instruments[0].id],
    )
    recent_stopped_job = repository.approve_backfill_job(recent_stopped_plan.plan_id)
    repository.control_backfill_job(recent_stopped_job.id, "stop")
    old_stopped_plan = repository.create_backfill_plan(
        "source_candle",
        start_at,
        end_at,
        [instruments[1].id],
    )
    old_stopped_job = repository.approve_backfill_job(old_stopped_plan.plan_id)
    repository.control_backfill_job(old_stopped_job.id, "stop")
    repository._execute(  # noqa: SLF001 - API 필터 검증을 위해 생성 시각만 고정한다.
        "UPDATE backfill_jobs SET created_at = ?, updated_at = ? WHERE id = ?",
        (
            (now_kst() - timedelta(days=40)).isoformat(),
            (now_kst() - timedelta(days=40)).isoformat(),
            old_stopped_job.id,
        ),
    )
    running_plan = repository.create_backfill_plan(
        "source_candle",
        start_at,
        end_at,
        [item.id for item in instruments[2:5]],
    )
    running_job = repository.approve_backfill_job(running_plan.plan_id)
    repository.claim_next_backfill_job()
    repository.mark_backfill_target(
        running_job.id,
        instruments[2].id,
        "succeeded",
        end_at,
    )
    repository.mark_backfill_target(
        running_job.id,
        instruments[3].id,
        "running",
        start_at,
    )
    repository.record_backfill_target_progress(
        running_job.id,
        instruments[3].id,
        processed_missing_range_count=3,
        estimated_missing_range_count=9,
        rows_written_count=120,
        last_completed_at=start_at + timedelta(minutes=2),
    )

    response = client.get("/v1/backfill/jobs")

    assert response.status_code == 200
    jobs_by_id = {item["id"]: item for item in response.json()["items"]}
    assert old_stopped_job.id not in jobs_by_id
    assert recent_stopped_job.id in jobs_by_id
    running = jobs_by_id[running_job.id]
    assert running["progressPercent"] == "44.44"
    assert running["totalTargetCount"] == 3
    assert running["completedTargetCount"] == 1
    assert running["runningTargetIndex"] == 2
    assert running["currentTarget"]["id"] == instruments[3].id
    assert running["currentTargetBackfillRowCount"] == 120
    assert running["processedMissingRangeCount"] == 3
    assert running["estimatedMissingRangeCount"] == 9
    assert running["estimatedRequestCount"] == running_plan.estimated_request_count


def test_dashboard_panel_endpoints_return_summary_slices() -> None:
    client = seeded_client()
    summary = client.get("/v1/dashboard/summary").json()

    overview = client.get("/v1/dashboard/overview")
    targets = client.get("/v1/dashboard/targets")
    coverage = client.get("/v1/dashboard/coverage")
    collection_activity = client.get("/v1/dashboard/collection-activity")
    realtime_heatmap = client.get("/v1/dashboard/realtime-heatmap")
    storage_breakdown = client.get("/v1/dashboard/storage-breakdown")
    operations_trend = client.get("/v1/dashboard/operations-trend")
    missing_ranges = client.get("/v1/dashboard/missing-ranges")
    audit_log_summary = client.get("/v1/dashboard/audit-log-summary")

    assert overview.status_code == 200
    assert overview.json()["status"] == summary["status"]
    assert overview.json()["totals"] == summary["totals"]
    assert overview.json()["alerts"] == summary["alerts"]
    assert overview.json()["healthChecks"] == summary["healthChecks"]
    assert overview.json()["metricPrinciples"] == summary["metricPrinciples"]
    assert overview.json()["recommendedRefreshSeconds"] == 10

    assert targets.status_code == 200
    assert without_relative_freshness(targets.json()["items"]) == without_relative_freshness(
        summary["targets"]
    )
    assert targets.json()["total"] == 50
    assert targets.json()["limit"] == 50
    assert targets.json()["offset"] == 0
    assert targets.json()["recommendedRefreshSeconds"] == 15

    assert coverage.status_code == 200
    assert coverage.json()["items"] == summary["coverage"][:50]
    assert coverage.json()["total"] == 150
    assert coverage.json()["recommendedRefreshSeconds"] == 30

    assert collection_activity.status_code == 200
    assert collection_activity.json()["items"] == summary["collectionActivity"]
    assert collection_activity.json()["recommendedRefreshSeconds"] == 15

    assert realtime_heatmap.status_code == 200
    assert realtime_heatmap.json()["items"] == summary["realtimeCollectionHeatmap"]
    assert realtime_heatmap.json()["total"] == 50
    assert realtime_heatmap.json()["recommendedRefreshSeconds"] == 10

    assert storage_breakdown.status_code == 200
    assert storage_breakdown.json()["items"] == summary["storageBreakdown"]
    assert storage_breakdown.json()["recommendedRefreshSeconds"] == 60

    assert operations_trend.status_code == 200
    assert operations_trend.json()["items"] == summary["operationsTrend"]
    assert operations_trend.json()["recommendedRefreshSeconds"] == 60

    assert missing_ranges.status_code == 200
    assert missing_ranges.json()["items"] == summary["missingRangeTop"]
    assert missing_ranges.json()["total"] == 5
    assert missing_ranges.json()["recommendedRefreshSeconds"] == 60

    assert audit_log_summary.status_code == 200
    assert audit_log_summary.json()["targetChangeCount24h"] == summary["auditLogSummary"][
        "targetChangeCount24h"
    ]
    assert audit_log_summary.json()["backfillChangeCount24h"] == summary["auditLogSummary"][
        "backfillChangeCount24h"
    ]
    assert audit_log_summary.json()["latestChangeLabel"] == summary["auditLogSummary"][
        "latestChangeLabel"
    ]
    assert audit_log_summary.json()["recommendedRefreshSeconds"] == 60


def test_dashboard_panel_pagination_and_validation() -> None:
    client = seeded_client()

    targets = client.get("/v1/dashboard/targets", params={"limit": 10, "offset": 5})
    coverage = client.get("/v1/dashboard/coverage", params={"limit": 20, "offset": 10})
    heatmap = client.get("/v1/dashboard/realtime-heatmap", params={"limit": 7, "offset": 3})
    missing = client.get("/v1/dashboard/missing-ranges", params={"limit": 2, "offset": 1})

    assert targets.status_code == 200
    assert len(targets.json()["items"]) == 10
    assert targets.json()["total"] == 50
    assert targets.json()["limit"] == 10
    assert targets.json()["offset"] == 5
    assert coverage.status_code == 200
    assert len(coverage.json()["items"]) == 20
    assert coverage.json()["total"] == 150
    assert heatmap.status_code == 200
    assert len(heatmap.json()["items"]) == 7
    assert heatmap.json()["total"] == 50
    assert missing.status_code == 200
    assert len(missing.json()["items"]) == 2
    assert missing.json()["total"] == 5

    invalid_queries = [
        ("/v1/dashboard/targets", {"limit": 101}),
        ("/v1/dashboard/coverage", {"limit": 0}),
        ("/v1/dashboard/realtime-heatmap", {"offset": -1}),
        ("/v1/dashboard/missing-ranges", {"limit": -1}),
    ]
    for path, params in invalid_queries:
        response = client.get(path, params=params)
        assert response.status_code == 422
        assert response.json()["code"] == "VALIDATION_ERROR"


def test_dashboard_refresh_config_override_and_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "operations-api.yaml"
    config_path.write_text(
        "\n".join(
            [
                "dashboardRefreshSeconds:",
                "  overview: 3",
                "  coverage: 31",
                "  auditLogSummary: 61",
            ]
        )
    )
    monkeypatch.setenv("GOODMONEYING_DASHBOARD_REFRESH_CONFIG", str(config_path))

    client = seeded_client()

    assert client.get("/v1/dashboard/overview").json()["recommendedRefreshSeconds"] == 3
    assert client.get("/v1/dashboard/coverage").json()["recommendedRefreshSeconds"] == 31
    assert (
        client.get("/v1/dashboard/audit-log-summary").json()["recommendedRefreshSeconds"]
        == 61
    )
    assert client.get("/v1/dashboard/targets").json()["recommendedRefreshSeconds"] == 15


def test_dashboard_refresh_config_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "operations-api.yaml"
    config_path.write_text("dashboardRefreshSeconds:\n  overview: 0\n")
    monkeypatch.setenv("GOODMONEYING_DASHBOARD_REFRESH_CONFIG", str(config_path))

    with pytest.raises(ValueError, match="overview"):
        create_app(SQLiteOperationsRepository())


def test_dashboard_panel_endpoints_respond_within_three_seconds() -> None:
    client = seeded_client()
    paths = [
        "/v1/dashboard/overview",
        "/v1/dashboard/targets",
        "/v1/dashboard/coverage",
        "/v1/dashboard/collection-activity",
        "/v1/dashboard/realtime-heatmap",
        "/v1/dashboard/storage-breakdown",
        "/v1/dashboard/operations-trend",
        "/v1/dashboard/missing-ranges",
        "/v1/dashboard/audit-log-summary",
    ]

    for path in paths:
        warmup = client.get(path)
        assert warmup.status_code == 200
        for _ in range(3):
            start = perf_counter()
            response = client.get(path)
            elapsed = perf_counter() - start
            assert response.status_code == 200
            assert elapsed < 3


def test_dashboard_coverage_segments_are_loaded_lazily() -> None:
    client = seeded_client()
    dashboard = client.get("/v1/dashboard/summary").json()
    instrument_id = dashboard["targets"][0]["instrument"]["id"]

    segments = client.get(f"/v1/collection-targets/{instrument_id}/coverage-segments")

    assert segments.status_code == 200
    assert segments.json()["instrumentId"] == instrument_id
    assert len(segments.json()["items"]) > 0
    assert segments.json()["items"][0]["status"] in {"collected", "missing"}
    assert segments.json()["items"][0]["offsetPercent"] == "0"


def test_write_apis_require_operator_token() -> None:
    client = seeded_client()
    universe = client.get("/v1/candidate-universe").json()
    instrument_ids = [entry["instrument"]["id"] for entry in universe["entries"][:50]]

    response = client.put("/v1/collection-targets", json={"instrumentIds": instrument_ids})

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


def test_collection_targets_allow_up_to_50_candidate_instruments() -> None:
    client = seeded_client()
    universe = client.get("/v1/candidate-universe").json()
    instrument_ids = [entry["instrument"]["id"] for entry in universe["entries"][:2]]

    response = client.put(
        "/v1/collection-targets",
        headers={"X-Operator-Token": "local-dev-token"},
        json={"instrumentIds": instrument_ids},
    )

    assert response.status_code == 200
    assert len(response.json()["targets"]) == 2


def test_candidate_universe_remains_available_after_target_update() -> None:
    client = seeded_client()
    universe = client.get("/v1/candidate-universe").json()
    instrument_ids = [entry["instrument"]["id"] for entry in universe["entries"][:50]]

    update = client.put(
        "/v1/collection-targets",
        headers={"X-Operator-Token": "local-dev-token"},
        json={"instrumentIds": instrument_ids, "reason": "E2E baseline reset"},
    )
    refreshed = client.get("/v1/candidate-universe")

    assert update.status_code == 200
    assert refreshed.status_code == 200
    assert len(refreshed.json()["entries"]) == 100


def test_collection_targets_reject_more_than_50_instruments() -> None:
    client = seeded_client()
    universe = client.get("/v1/candidate-universe").json()
    instrument_ids = [entry["instrument"]["id"] for entry in universe["entries"][:51]]

    response = client.put(
        "/v1/collection-targets",
        headers={"X-Operator-Token": "local-dev-token"},
        json={"instrumentIds": instrument_ids},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_backfill_job_start_and_control() -> None:
    client = seeded_client()
    universe = client.get("/v1/candidate-universe").json()
    instrument_ids = [entry["instrument"]["id"] for entry in universe["entries"][:2]]
    start_at = (now_kst() - timedelta(hours=1)).isoformat()
    end_at = now_kst().isoformat()

    job = client.post(
        "/v1/backfill/jobs",
        headers={"X-Operator-Token": "local-dev-token"},
        json={
            "dataType": "source_candle",
            "targetStartAt": start_at,
            "targetEndAt": end_at,
            "instrumentIds": instrument_ids,
        },
    )
    assert job.status_code == 201
    assert job.json()["status"] == "pending"
    assert [target["id"] for target in job.json()["targets"]] == instrument_ids

    paused = client.post(
        f"/v1/backfill/jobs/{job.json()['id']}/pause",
        headers={"X-Operator-Token": "local-dev-token"},
    )
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"

    stopped = client.post(
        f"/v1/backfill/jobs/{job.json()['id']}/stop",
        headers={"X-Operator-Token": "local-dev-token"},
    )
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "stopped"

    deleted = client.delete(
        f"/v1/backfill/jobs/{job.json()['id']}",
        headers={"X-Operator-Token": "local-dev-token"},
    )
    assert deleted.status_code == 204
    assert all(
        item["id"] != job.json()["id"]
        for item in client.get("/v1/backfill/jobs").json()["items"]
    )


def test_backfill_jobs_return_repository_progress() -> None:
    repository, client = seeded_repository_and_client()
    universe = client.get("/v1/candidate-universe").json()
    instrument_ids = [entry["instrument"]["id"] for entry in universe["entries"][:2]]
    start_at = (now_kst() - timedelta(hours=1)).isoformat()
    end_at = now_kst().isoformat()

    job = client.post(
        "/v1/backfill/jobs",
        headers={"X-Operator-Token": "local-dev-token"},
        json={
            "dataType": "source_candle",
            "targetStartAt": start_at,
            "targetEndAt": end_at,
            "instrumentIds": instrument_ids,
        },
    ).json()
    repository.claim_next_backfill_job()

    repository.mark_backfill_target(job["id"], instrument_ids[0], "succeeded", now_kst())

    jobs = client.get("/v1/backfill/jobs")

    assert jobs.status_code == 200
    item = jobs.json()["items"][0]
    assert item["status"] == "running"
    assert item["progressPercent"] == "50"
    assert item["targetStartAt"] == start_at
    assert item["targetEndAt"] == end_at
    assert [target["id"] for target in item["targets"]] == instrument_ids


def test_candle_endpoint_rejects_unsupported_unit_and_invalid_range() -> None:
    client = seeded_client()
    instrument_id = client.get("/v1/market-list").json()["rows"][0]["instrument"]["id"]
    start_at = (now_kst() - timedelta(hours=1)).isoformat()
    end_at = now_kst().isoformat()

    unsupported_unit = client.get(
        f"/v1/instruments/{instrument_id}/candles",
        params={"unit": "2m", "from": start_at, "to": end_at},
    )
    invalid_range = client.get(
        f"/v1/instruments/{instrument_id}/candles",
        params={"unit": "1m", "from": end_at, "to": start_at},
    )

    assert unsupported_unit.status_code == 400
    assert unsupported_unit.json()["code"] == "INVALID_CANDLE_QUERY"
    assert invalid_range.status_code == 400
    assert invalid_range.json()["code"] == "INVALID_CANDLE_QUERY"
