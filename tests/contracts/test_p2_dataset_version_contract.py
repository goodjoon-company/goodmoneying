from pathlib import Path

import yaml

OPENAPI = Path("docs/contracts/api/openapi.yaml")


def test_P2_5_OpenAPI는_생성작업과_불변버전_조회경계를_정의한다() -> None:
    document = yaml.safe_load(OPENAPI.read_text())
    paths = document["paths"]

    create = paths["/v1/dataset-builds"]["post"]
    assert create["security"] == [{"OperatorToken": []}]
    assert "202" in create["responses"]
    assert (
        create["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/CreateDatasetBuildRequest"
    )

    assert "get" in paths["/v1/dataset-builds/{buildId}"]
    assert "get" in paths["/v1/dataset-versions"]
    assert "get" in paths["/v1/dataset-versions/{datasetVersionId}"]
    assert "get" in paths["/v1/dataset-versions/{datasetVersionId}/coverage"]
    assert "get" in paths["/v1/dataset-versions/{datasetVersionId}/series"]


def test_P2_5_생성계약은_명령_선택_인과성과_결측정책을_고정한다() -> None:
    document = yaml.safe_load(OPENAPI.read_text())
    schemas = document["components"]["schemas"]
    request = schemas["CreateDatasetBuildRequest"]

    assert request["additionalProperties"] is False
    assert set(request["required"]) == {
        "requestId",
        "idempotencyKey",
        "actorId",
        "requestedAt",
        "reason",
        "selection",
        "policies",
    }
    assert set(schemas["DatasetSelection"]["required"]) == {"asOf", "from", "to", "series"}
    assert schemas["DatasetPolicies"]["properties"]["availabilityPolicy"]["const"] == (
        "point_in_time_v1"
    )
    assert schemas["DatasetPolicies"]["properties"]["fillPolicy"]["enum"] == [
        "none",
        "no_trade_carry_forward_v1",
    ]
    assert schemas["DatasetPolicies"]["properties"]["missingPolicy"]["enum"] == [
        "fail",
        "null",
        "drop",
    ]


def test_P2_5_series_조회는_dataset_bound_cursor와_고정된_품질을_노출한다() -> None:
    document = yaml.safe_load(OPENAPI.read_text())
    operation = document["paths"]["/v1/dataset-versions/{datasetVersionId}/series"]["get"]
    parameters = {item["name"]: item for item in operation["parameters"]}

    assert {"datasetVersionId", "seriesId", "from", "to", "pageSize", "cursor"} <= set(
        parameters
    )
    assert parameters["cursor"]["schema"]["type"] == ["string", "null"]
    assert "데이터셋" in parameters["cursor"]["description"]
    point = document["components"]["schemas"]["DatasetSeriesPoint"]
    assert {
        "occurredAt",
        "knowledgeAt",
        "quality",
        "contentHash",
        "values",
    } <= set(point["required"])
    assert point["properties"]["quality"]["enum"] == [
        "available",
        "no_trade",
        "missing",
        "unavailable",
        "unverified",
    ]
    assert "합성하지" in operation["responses"]["200"]["description"]


def test_P2_5_coverage는_품질판정의_지식시각을_노출한다() -> None:
    document = yaml.safe_load(OPENAPI.read_text())
    coverage = document["components"]["schemas"]["DatasetCoverageItem"]

    assert "knowledgeAt" in coverage["required"]
    assert coverage["properties"]["knowledgeAt"] == {
        "type": "string",
        "format": "date-time",
        "description": "품질 상태를 알게 된 UTC 시각.",
    }


def test_P2_5_null_drop과_carry_forward는_P3_P4_소비정책이다() -> None:
    document = yaml.safe_load(OPENAPI.read_text())
    policies = document["components"]["schemas"]["DatasetPolicies"]

    assert "P3/P4" in policies["properties"]["fillPolicy"]["description"]
    assert "P3/P4" in policies["properties"]["missingPolicy"]["description"]


def test_P2_5_버전목록은_최초_ID상한을_고정한_cursor를_사용한다() -> None:
    document = yaml.safe_load(OPENAPI.read_text())
    operation = document["paths"]["/v1/dataset-versions"]["get"]
    parameters = {item["name"]: item for item in operation["parameters"]}

    assert {"pageSize", "cursor"} == set(parameters)
    assert parameters["pageSize"]["schema"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 100,
        "default": 50,
    }
    assert "최초 데이터셋 버전 ID 상한" in parameters["cursor"]["description"]
    response = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert response["$ref"] == "#/components/schemas/DatasetVersionsResponse"
