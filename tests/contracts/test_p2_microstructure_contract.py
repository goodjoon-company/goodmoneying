from pathlib import Path

import yaml

MIGRATION = Path("docs/contracts/db/migrations/20260717001200_p2_microstructure.sql")
OPENAPI = Path("docs/contracts/api/openapi.yaml")


def test_P2_4_migration은_미시구조_정의_물질화_계보와_재계산_큐를_정의한다() -> None:
    sql = MIGRATION.read_text()
    for table in (
        "realtime_connection_sessions",
        "realtime_connection_quality_intervals",
        "microstructure_definition_versions",
        "microstructure_materializations",
        "microstructure_statistics",
        "microstructure_invalidations",
    ):
        assert f"CREATE TABLE {table}" in sql
    for column in (
        "orderbook_snapshot_through_id",
        "trade_event_through_id",
        "source_receipt_through_id",
        "source_candle_revision_id",
        "quality_event_through_id",
        "connection_quality_through_id",
        "knowledge_at",
        "source_as_of",
        "input_lineage_hash",
        "parent_statistic_id",
        "orderbook_quality",
        "trade_quality",
    ):
        assert column in sql
    assert "reject_microstructure_immutable_mutation" in sql
    assert "enqueue_microstructure_invalidation" in sql
    assert "enqueue_source_candle_microstructure_invalidation" in sql
    assert "enqueue_quality_microstructure_invalidation" in sql
    assert "microstructure-invalidations-active-bucket" in sql
    assert "source_receipt_id BIGINT REFERENCES source_receipts(id)" in sql
    assert "reject_conflicting_trade_event" in sql


def test_P2_4_연결_품질은_무체결과_수집유실을_구분할_증거를_보존한다() -> None:
    sql = MIGRATION.read_text()

    for column in (
        "connection_id",
        "subscription_generation",
        "connected_at",
        "disconnected_at",
        "disconnect_reason",
        "first_frame_sequence",
        "last_frame_sequence",
        "range_start_at",
        "range_end_at",
        "quality",
    ):
        assert column in sql
    for quality in ("available", "missing", "unavailable", "unverified"):
        assert f"'{quality}'" in sql


def test_P2_4_OpenAPI는_범위_asOf_버전_cursor와_명시적_상태를_노출한다() -> None:
    document = yaml.safe_load(OPENAPI.read_text())
    operation = document["paths"]["/v1/instruments/{instrumentId}/microstructure-statistics"]["get"]
    names = {item["name"] for item in operation["parameters"] if "name" in item}
    assert {"from", "to", "asOf", "calculationVersion", "pageSize", "cursor"} <= names

    point = document["components"]["schemas"]["MicrostructureStatistic"]
    assert {
        "startedAt",
        "calculationVersion",
        "orderbookStatus",
        "orderbookQuality",
        "tradeStatus",
        "tradeQuality",
        "executionStrengthStatus",
        "sourceCandleRevisionId",
        "qualityEventThroughId",
        "connectionQualityThroughId",
        "orderbookSnapshotThroughId",
        "tradeEventThroughId",
        "sourceReceiptThroughId",
        "sourceAsOf",
        "knowledgeAt",
        "contentHash",
    } <= set(point["required"])
