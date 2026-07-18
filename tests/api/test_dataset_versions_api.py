from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from goodmoneying_api.main import (
    DatasetApiIdempotencyConflictError,
    create_app,
)
from goodmoneying_shared.dataset_version_store import DatasetCursorMismatchError
from goodmoneying_shared.sqlite_repository import SQLiteOperationsRepository

HASH_A = "a" * 64
HASH_B = "b" * 64


def _request() -> dict[str, Any]:
    return {
        "requestId": "dataset-request-1",
        "idempotencyKey": "dataset-key-1",
        "actorId": "operator:test",
        "requestedAt": "2026-07-17T06:00:00Z",
        "reason": "첫 연구 데이터셋",
        "selection": {
            "asOf": "2026-07-17T05:00:00Z",
            "from": "2026-07-17T00:00:00Z",
            "to": "2026-07-17T02:00:00Z",
            "series": [
                {
                    "instrumentId": 41,
                    "dataKind": "candle",
                    "unit": "1m",
                    "definitionSetHash": None,
                    "calculationVersion": "source-candle-v1",
                }
            ],
        },
        "policies": {
            "availabilityPolicy": "point_in_time_v1",
            "fillPolicy": "none",
            "missingPolicy": "fail",
        },
    }


def _client(repository: FakeDatasetVersionRepository) -> TestClient:
    return TestClient(
        create_app(
            SQLiteOperationsRepository(),
            dataset_version_repository=repository,
        )
    )


def test_데이터셋_생성은_운영토큰을_요구하고_프런티어_고정작업을_202로_반환한다() -> None:
    repository = FakeDatasetVersionRepository()
    client = _client(repository)

    unauthorized = client.post("/v1/dataset-builds", json=_request())
    accepted = client.post(
        "/v1/dataset-builds",
        headers={"X-Operator-Token": "local-dev-token"},
        json=_request(),
    )

    assert unauthorized.status_code == 401
    assert accepted.status_code == 202
    assert accepted.json() == repository.build
    assert repository.create_count == 1
    assert repository.create_arguments is not None
    assert repository.create_arguments["idempotency_key"] == "dataset-key-1"
    assert repository.create_arguments["selection"]["asOf"] == datetime(
        2026, 7, 17, 5, tzinfo=UTC
    )


def test_같은_멱등키와_본문은_재생하고_다른_본문은_409로_거부한다() -> None:
    repository = FakeDatasetVersionRepository()
    client = _client(repository)
    headers = {"X-Operator-Token": "local-dev-token"}

    first = client.post("/v1/dataset-builds", headers=headers, json=_request())
    replay = client.post("/v1/dataset-builds", headers=headers, json=_request())
    changed = _request()
    changed["reason"] = "다른 의미"
    conflict = client.post("/v1/dataset-builds", headers=headers, json=changed)

    assert first.status_code == replay.status_code == 202
    assert first.json()["buildId"] == replay.json()["buildId"]
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "DATASET_IDEMPOTENCY_CONFLICT"
    assert repository.mutation_count == 1


def test_저장소의_유효한_도메인_거부는_안정된_422로_반환한다() -> None:
    repository = FakeDatasetVersionRepository(validation_error=True)
    client = _client(repository)

    response = client.post(
        "/v1/dataset-builds",
        headers={"X-Operator-Token": "local-dev-token"},
        json=_request(),
    )

    assert response.status_code == 422
    assert response.json() == {
        "code": "INVALID_DATASET_BUILD",
        "message": "asOf 시점의 시장 상태가 없다.",
    }


def test_생성은_UTC_반개방범위와_v1_채움정책을_검증한다() -> None:
    repository = FakeDatasetVersionRepository()
    client = _client(repository)
    headers = {"X-Operator-Token": "local-dev-token"}

    non_utc = _request()
    non_utc["selection"]["from"] = "2026-07-17T09:00:00+09:00"
    reversed_range = _request()
    reversed_range["selection"]["from"] = reversed_range["selection"]["to"]
    future_range = _request()
    future_range["selection"]["to"] = "2026-07-17T05:01:00Z"
    future_as_of = _request()
    future_as_of["selection"]["asOf"] = "2026-07-17T06:00:01Z"
    invalid_fill = _request()
    invalid_fill["selection"]["series"][0]["dataKind"] = "microstructure"
    invalid_fill["policies"]["fillPolicy"] = "no_trade_carry_forward_v1"
    ambiguous_version = _request()
    del ambiguous_version["selection"]["series"][0]["definitionSetHash"]

    for payload in (
        non_utc,
        reversed_range,
        future_range,
        future_as_of,
        invalid_fill,
        ambiguous_version,
    ):
        response = client.post("/v1/dataset-builds", headers=headers, json=payload)
        assert response.status_code == 422
    assert repository.create_count == 0


def test_작업_버전_커버리지_series_GET은_저장소를_읽기만_한다() -> None:
    repository = FakeDatasetVersionRepository()
    client = _client(repository)
    mutation_count = repository.mutation_count

    build = client.get("/v1/dataset-builds/7")
    version = client.get("/v1/dataset-versions/11")
    coverage = client.get("/v1/dataset-versions/11/coverage")
    series = client.get(
        "/v1/dataset-versions/11/series",
        params={
            "seriesId": 101,
            "from": "2026-07-17T00:00:00Z",
            "to": "2026-07-17T01:00:00Z",
            "pageSize": 50,
            "cursor": "dataset-bound-cursor",
        },
    )

    assert build.status_code == version.status_code == coverage.status_code == 200
    assert series.status_code == 200
    assert series.json()["items"][0]["quality"] == "available"
    assert repository.series_arguments == {
        "dataset_version_id": 11,
        "series_id": 101,
        "from_at": datetime(2026, 7, 17, 0, tzinfo=UTC),
        "to_at": datetime(2026, 7, 17, 1, tzinfo=UTC),
        "page_size": 50,
        "cursor": "dataset-bound-cursor",
    }
    assert repository.mutation_count == mutation_count
    assert repository.read_count == 4


def test_작업_GET은_retry_wait와_dead_letter_운영상태를_손실없이_반환한다() -> None:
    repository = FakeDatasetVersionRepository()
    repository.build.update(
        {
            "status": "retry_wait",
            "attemptCount": 2,
            "maxAttempts": 3,
            "nextRetryAt": "2026-07-17T06:05:00Z",
            "deadLetterReason": None,
        }
    )
    client = _client(repository)

    retry_wait = client.get("/v1/dataset-builds/7")
    repository.build.update(
        {
            "status": "dead_letter",
            "nextRetryAt": None,
            "deadLetterReason": "unexpected_publication_error",
        }
    )
    dead_letter = client.get("/v1/dataset-builds/7")

    assert retry_wait.status_code == dead_letter.status_code == 200
    assert retry_wait.json()["nextRetryAt"] == "2026-07-17T06:05:00Z"
    assert dead_letter.json()["deadLetterReason"] == "unexpected_publication_error"


def test_작업목록_GET은_in_flight_build를_새로고침_뒤에도_재발견한다() -> None:
    repository = FakeDatasetVersionRepository()
    client = _client(repository)
    mutation_count = repository.mutation_count

    response = client.get(
        "/v1/dataset-builds",
        params={"pageSize": 25, "cursor": "build-id-ceiling-cursor"},
    )

    assert response.status_code == 200
    assert response.json() == {"items": [repository.build], "nextCursor": None}
    assert repository.build_list_arguments == {
        "page_size": 25,
        "cursor": "build-id-ceiling-cursor",
    }
    assert repository.mutation_count == mutation_count
    assert repository.read_count == 1


def test_버전목록_GET은_안정된_cursor를_전달하고_저장소를_읽기만_한다() -> None:
    repository = FakeDatasetVersionRepository()
    client = _client(repository)
    mutation_count = repository.mutation_count

    response = client.get(
        "/v1/dataset-versions",
        params={"pageSize": 25, "cursor": "version-id-ceiling-cursor"},
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["datasetVersionId"] == 11
    assert response.json()["nextCursor"] is None
    assert repository.list_arguments == {
        "page_size": 25,
        "cursor": "version-id-ceiling-cursor",
    }
    assert repository.mutation_count == mutation_count
    assert repository.read_count == 1


def test_존재하지_않는_데이터셋_자원은_404를_반환한다() -> None:
    repository = FakeDatasetVersionRepository(not_found=True)
    client = _client(repository)

    assert client.get("/v1/dataset-builds/999").status_code == 404
    assert client.get("/v1/dataset-versions/999").status_code == 404
    assert client.get("/v1/dataset-versions/999/coverage").status_code == 404
    assert (
        client.get(
            "/v1/dataset-versions/999/series",
            params={
                "seriesId": 1,
                "from": "2026-07-17T00:00:00Z",
                "to": "2026-07-17T01:00:00Z",
            },
        ).status_code
        == 404
    )


def test_데이터셋_식별자는_양수여야_하고_저장소까지_전달하지_않는다() -> None:
    repository = FakeDatasetVersionRepository()
    client = _client(repository)

    assert client.get("/v1/dataset-builds/0").status_code == 422
    assert client.get("/v1/dataset-versions/0").status_code == 422
    assert client.get("/v1/dataset-versions/0/coverage").status_code == 422
    assert (
        client.get(
            "/v1/dataset-versions/0/series",
            params={
                "seriesId": 1,
                "from": "2026-07-17T00:00:00Z",
                "to": "2026-07-17T01:00:00Z",
            },
        ).status_code
        == 422
    )
    assert repository.read_count == 0


def test_다른_조회문맥의_dataset_cursor는_안정된_409를_반환한다() -> None:
    repository = FakeDatasetVersionRepository()
    client = _client(repository)

    list_response = client.get(
        "/v1/dataset-versions", params={"cursor": "wrong-context"}
    )
    series_response = client.get(
        "/v1/dataset-versions/11/series",
        params={
            "seriesId": 101,
            "from": "2026-07-17T00:00:00Z",
            "to": "2026-07-17T01:00:00Z",
            "cursor": "wrong-context",
        },
    )

    assert list_response.status_code == series_response.status_code == 409
    assert list_response.json()["code"] == "DATASET_CURSOR_CONTEXT_MISMATCH"
    assert series_response.json()["code"] == "DATASET_CURSOR_CONTEXT_MISMATCH"


class FakeDatasetVersionRepository:
    def __init__(
        self, *, not_found: bool = False, validation_error: bool = False
    ) -> None:
        self.not_found = not_found
        self.validation_error = validation_error
        self.create_count = 0
        self.mutation_count = 0
        self.read_count = 0
        self.create_arguments: dict[str, Any] | None = None
        self.series_arguments: dict[str, Any] | None = None
        self.build_list_arguments: dict[str, Any] | None = None
        self.list_arguments: dict[str, Any] | None = None
        self._request_by_key: dict[str, dict[str, Any]] = {}
        self.build = {
            "buildId": 7,
            "requestId": "dataset-request-1",
            "idempotencyKey": "dataset-key-1",
            "actorId": "operator:test",
            "requestedAt": "2026-07-17T06:00:00Z",
            "frozenAt": "2026-07-17T06:00:01Z",
            "status": "pending",
            "attemptCount": 0,
            "maxAttempts": 3,
            "nextRetryAt": None,
            "deadLetterReason": None,
            "datasetVersionId": None,
            "errorCode": None,
            "errorMessage": None,
        }

    def create_build(self, **arguments: Any) -> dict[str, Any]:
        self.create_count += 1
        if self.validation_error:
            raise ValueError("asOf 시점의 시장 상태가 없다.")
        key = str(arguments["idempotency_key"])
        previous = self._request_by_key.get(key)
        if previous is not None:
            if previous != arguments:
                raise DatasetApiIdempotencyConflictError("멱등 키의 요청 내용이 다르다.")
            return self.build
        self._request_by_key[key] = arguments
        self.create_arguments = arguments
        self.mutation_count += 1
        return self.build

    def get_build(self, build_id: int) -> dict[str, Any] | None:
        self.read_count += 1
        return None if self.not_found else self.build

    def list_builds(self, **arguments: Any) -> dict[str, Any]:
        self.read_count += 1
        self.build_list_arguments = arguments
        return {"items": [] if self.not_found else [self.build], "nextCursor": None}

    def get_version(self, dataset_version_id: int) -> dict[str, Any] | None:
        self.read_count += 1
        if self.not_found:
            return None
        return {
            "datasetVersionId": dataset_version_id,
            "schemaVersion": "dataset-v1",
            "asOf": "2026-07-17T05:00:00Z",
            "from": "2026-07-17T00:00:00Z",
            "to": "2026-07-17T02:00:00Z",
            "contentHash": HASH_A,
            "availabilityPolicy": "point_in_time_v1",
            "fillPolicy": "none",
            "missingPolicy": "fail",
            "createdAt": "2026-07-17T06:00:02Z",
            "series": [
                {
                    "seriesId": 101,
                    "instrumentId": 41,
                    "dataKind": "candle",
                    "unit": "1m",
                    "definitionSetHash": None,
                    "calculationVersion": "source-candle-v1",
                }
            ],
        }

    def list_versions(self, **arguments: Any) -> dict[str, Any]:
        self.read_count += 1
        self.list_arguments = arguments
        if arguments["cursor"] == "wrong-context":
            raise DatasetCursorMismatchError("목록 cursor 문맥이 다르다.")
        version = self.get_version(11)
        self.read_count -= 1
        return {"items": [version], "nextCursor": None}

    def get_coverage(self, dataset_version_id: int) -> dict[str, Any] | None:
        self.read_count += 1
        if self.not_found:
            return None
        return {
            "datasetVersionId": dataset_version_id,
            "snapshotHash": HASH_B,
            "requestedBucketCount": 120,
            "eligibleBucketCount": 120,
            "usableRatio": "1.0",
            "counts": {
                "available": 120,
                "no_trade": 0,
                "missing": 0,
                "unavailable": 0,
                "unverified": 0,
            },
            "items": [],
        }

    def get_series(self, **arguments: Any) -> dict[str, Any] | None:
        self.read_count += 1
        self.series_arguments = arguments
        if arguments["cursor"] == "wrong-context":
            raise DatasetCursorMismatchError("series cursor 문맥이 다르다.")
        if self.not_found:
            return None
        return {
            "datasetVersionId": arguments["dataset_version_id"],
            "seriesId": arguments["series_id"],
            "dataKind": "candle",
            "unit": "1m",
            "items": [
                {
                    "occurredAt": "2026-07-17T00:00:00Z",
                    "knowledgeAt": "2026-07-17T00:00:01Z",
                    "quality": "available",
                    "contentHash": HASH_A,
                    "values": {"close": "100.0"},
                }
            ],
            "nextCursor": None,
        }
