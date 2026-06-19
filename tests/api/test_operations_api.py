from __future__ import annotations

from datetime import timedelta

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
    assert first_target["instrument"]["marketCode"] == "KRW-BTC"
    assert first_target["overallStatus"] == "latest_collecting"
    assert first_target["overallStatusLabel"] == "최신수집중"
    assert first_target["plan"]["isContinuous"] is True
    assert first_target["plan"]["rangeTimeZone"] == "KST"
    assert first_target["coverageSegments"][0]["status"] == "collected"
    assert first_target["coverageSegments"][0]["offsetPercent"] == "0"
    assert totals["activeTargetLimit"] == 50
    assert totals["normalTargets"] + totals["warningTargets"] + totals["incidentTargets"] == 50
    assert totals["storageBytesToday"] > 0
    assert totals["storageBytesTodayDisplay"].endswith(("MB", "GB"))
    assert "failureRate24h" in totals
    assert dashboard.json()["healthChecks"][0]["title"]
    assert universe.status_code == 200
    assert len(universe.json()["entries"]) == 100
    assert universe.json()["entries"][0]["accTradePrice24hDisplay"].startswith("₩")
    assert "," in universe.json()["entries"][0]["accTradePrice24hDisplay"]
    assert universe.json()["entries"][0]["qualityStatus"] in {"normal", "warning", "incident"}
    assert universe.json()["entries"][0]["qualityDetail"]
    assert universe.json()["entries"][0]["collectionRangeDisplay"].endswith(" ~ NOW")
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
    assert detail.json()["duplicateRows24h"] >= 0
    assert detail.json()["tickerFreshnessLabel"].endswith("전")
    assert detail.json()["orderbookFreshnessLabel"].endswith("전")


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
