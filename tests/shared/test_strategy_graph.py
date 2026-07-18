from __future__ import annotations

from typing import Any, cast

from goodmoneying_shared.strategy_graph import (
    canonical_strategy_graph_hash,
    validate_strategy_graph,
)


def _valid_graph() -> dict[str, object]:
    return {
        "schema_version": "strategy-graph-v1",
        "nodes": [
            {
                "id": "input.close",
                "type": "dataset.candle.close",
                "config": {"missingDataPolicy": "fail"},
                "input_ports": [],
                "output_ports": [
                    {"name": "close", "dataType": "series.decimal", "timeframe": "1m"}
                ],
            },
            {
                "id": "indicator.sma.fast",
                "type": "indicator.sma",
                "config": {"windowPeriods": 3, "warmupPeriods": 3},
                "input_ports": [
                    {"name": "value", "dataType": "series.decimal", "timeframe": "1m"}
                ],
                "output_ports": [
                    {"name": "sma", "dataType": "series.decimal", "timeframe": "1m"}
                ],
            },
            {
                "id": "condition.cross",
                "type": "condition.greater_than",
                "config": {},
                "input_ports": [
                    {"name": "left", "dataType": "series.decimal", "timeframe": "1m"},
                    {"name": "right", "dataType": "series.decimal", "timeframe": "1m"},
                ],
                "output_ports": [
                    {"name": "decision", "dataType": "series.boolean", "timeframe": "1m"}
                ],
            },
            {
                "id": "bot.output",
                "type": "bot.signal",
                "config": {"signal": "enter_long"},
                "input_ports": [
                    {"name": "condition", "dataType": "series.boolean", "timeframe": "1m"}
                ],
                "output_ports": [
                    {
                        "name": "signal",
                        "dataType": "signal.order_intent",
                        "timeframe": "1m",
                    }
                ],
            },
        ],
        "edges": [
            {
                "from_node": "input.close",
                "from_port": "close",
                "to_node": "indicator.sma.fast",
                "to_port": "value",
            },
            {
                "from_node": "input.close",
                "from_port": "close",
                "to_node": "condition.cross",
                "to_port": "left",
            },
            {
                "from_node": "indicator.sma.fast",
                "from_port": "sma",
                "to_node": "condition.cross",
                "to_port": "right",
            },
            {
                "from_node": "condition.cross",
                "from_port": "decision",
                "to_node": "bot.output",
                "to_port": "condition",
            },
        ],
        "outputs": [{"node": "bot.output", "port": "signal"}],
    }


def test_전략_graph_hash는_노드와_edge_순서와_무관하다() -> None:
    graph = _valid_graph()
    nodes = cast(list[dict[str, Any]], graph["nodes"])
    edges = cast(list[dict[str, Any]], graph["edges"])
    reordered = {
        **graph,
        "nodes": list(reversed(nodes)),
        "edges": list(reversed(edges)),
    }

    assert validate_strategy_graph(graph).valid is True
    assert canonical_strategy_graph_hash(graph) == canonical_strategy_graph_hash(reordered)


def test_전략_validator는_실행전_안전오류를_안정된_code로_반환한다() -> None:
    graph = _valid_graph()
    edges = cast(list[dict[str, Any]], graph["edges"])
    nodes = cast(list[dict[str, Any]], graph["nodes"])
    graph["edges"] = [
        *edges,
        {
            "from_node": "bot.output",
            "from_port": "signal",
            "to_node": "indicator.sma.fast",
            "to_port": "value",
        },
    ]
    nodes[0]["config"] = {}
    nodes[1]["config"] = {
        "windowPeriods": 3,
        "warmupPeriods": 1,
        "lookAheadPeriods": 1,
    }

    result = validate_strategy_graph(graph)
    codes = {error.code for error in result.errors}

    assert result.valid is False
    assert {
        "cycle_detected",
        "port_type_mismatch",
        "look_ahead_detected",
        "missing_data_policy_required",
        "insufficient_warmup",
    } <= codes


def test_전략_validator는_주기와_파라미터와_output을_검증한다() -> None:
    graph = _valid_graph()
    nodes = cast(list[dict[str, Any]], graph["nodes"])
    nodes[1]["config"] = {"windowPeriods": 0, "warmupPeriods": 0}
    input_ports = cast(list[dict[str, Any]], nodes[2]["input_ports"])
    input_ports[1]["timeframe"] = "5m"
    graph["outputs"] = []

    result = validate_strategy_graph(graph)
    codes = {error.code for error in result.errors}

    assert {
        "parameter_out_of_range",
        "timeframe_incompatible",
        "missing_output",
    } <= codes
