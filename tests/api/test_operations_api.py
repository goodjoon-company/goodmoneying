from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from goodmoneying_api.main import create_app
from goodmoneying_shared.sqlite_repository import SQLiteOperationsRepository
from goodmoneying_shared.time import now_utc
from goodmoneying_worker.collector import seed_repository
from goodmoneying_worker.upbit_client import FixtureUpbitClient


def seeded_repository_and_client() -> tuple[SQLiteOperationsRepository, TestClient]:
    repository = SQLiteOperationsRepository()
    seed_repository(repository, FixtureUpbitClient())
    return repository, TestClient(create_app(repository))


def seeded_client() -> TestClient:
    return seeded_repository_and_client()[1]


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
        "quality_result",
    }
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
    assert universe.json()["entries"][0]["collectionRangeDisplay"].startswith("2026-01-01")
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


def test_backfill_plan_approval_and_control() -> None:
    client = seeded_client()
    universe = client.get("/v1/candidate-universe").json()
    instrument_ids = [entry["instrument"]["id"] for entry in universe["entries"][:2]]
    start_at = (now_utc() - timedelta(hours=1)).isoformat()
    end_at = now_utc().isoformat()

    plan = client.post(
        "/v1/backfill/plans",
        headers={"X-Operator-Token": "local-dev-token"},
        json={
            "dataType": "source_candle",
            "targetStartAt": start_at,
            "targetEndAt": end_at,
            "instrumentIds": instrument_ids,
        },
    )
    assert plan.status_code == 200

    job = client.post(
        "/v1/backfill/jobs",
        headers={"X-Operator-Token": "local-dev-token"},
        json={"planId": plan.json()["planId"]},
    )
    assert job.status_code == 201

    paused = client.post(
        f"/v1/backfill/jobs/{job.json()['id']}/pause",
        headers={"X-Operator-Token": "local-dev-token"},
    )
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"


def test_backfill_jobs_return_repository_progress() -> None:
    repository, client = seeded_repository_and_client()
    universe = client.get("/v1/candidate-universe").json()
    instrument_ids = [entry["instrument"]["id"] for entry in universe["entries"][:2]]
    start_at = (now_utc() - timedelta(hours=1)).isoformat()
    end_at = now_utc().isoformat()

    plan = client.post(
        "/v1/backfill/plans",
        headers={"X-Operator-Token": "local-dev-token"},
        json={
            "dataType": "source_candle",
            "targetStartAt": start_at,
            "targetEndAt": end_at,
            "instrumentIds": instrument_ids,
        },
    )
    job = client.post(
        "/v1/backfill/jobs",
        headers={"X-Operator-Token": "local-dev-token"},
        json={"planId": plan.json()["planId"]},
    ).json()

    repository.mark_backfill_target(job["id"], instrument_ids[0], "succeeded", now_utc())

    jobs = client.get("/v1/backfill/jobs")

    assert jobs.status_code == 200
    assert jobs.json()["items"][0]["status"] == "running"
    assert jobs.json()["items"][0]["progressPercent"] == "50"


def test_candle_endpoint_rejects_unsupported_unit_and_invalid_range() -> None:
    client = seeded_client()
    instrument_id = client.get("/v1/market-list").json()["rows"][0]["instrument"]["id"]
    start_at = (now_utc() - timedelta(hours=1)).isoformat()
    end_at = now_utc().isoformat()

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
