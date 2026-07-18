from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal, cast

StrategyValidationCode = Literal[
    "cycle_detected",
    "port_type_mismatch",
    "timeframe_incompatible",
    "look_ahead_detected",
    "parameter_out_of_range",
    "missing_data_policy_required",
    "insufficient_warmup",
    "missing_output",
]

_VALIDATION_CODES: set[str] = {
    "cycle_detected",
    "port_type_mismatch",
    "timeframe_incompatible",
    "look_ahead_detected",
    "parameter_out_of_range",
    "missing_data_policy_required",
    "insufficient_warmup",
    "missing_output",
}
_MISSING_DATA_POLICIES = {"fail", "null", "drop"}
_TIMEFRAMES = {"1m", "3m", "5m", "10m", "15m", "30m", "1h", "4h", "1d", "1w", "1M"}


@dataclass(frozen=True)
class StrategyValidationError:
    code: StrategyValidationCode
    node_id: str | None = None
    edge_index: int | None = None
    message: str = ""


@dataclass(frozen=True)
class StrategyValidationResult:
    valid: bool
    errors: tuple[StrategyValidationError, ...]
    graph_hash: str

    def to_api(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "errors": [
                {
                    "code": error.code,
                    "nodeId": error.node_id,
                    "edgeIndex": error.edge_index,
                    "message": error.message,
                }
                for error in self.errors
            ],
            "graphHash": self.graph_hash,
        }


def canonical_strategy_graph_hash(graph: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical_json_bytes(_canonical_graph(graph))).hexdigest()


def validate_strategy_graph(graph: Mapping[str, object]) -> StrategyValidationResult:
    graph_hash = canonical_strategy_graph_hash(graph)
    errors: list[StrategyValidationError] = []
    nodes = _mapping_list(graph.get("nodes"))
    edges = _mapping_list(graph.get("edges"))
    outputs = _mapping_list(graph.get("outputs"))
    nodes_by_id = {str(node.get("id")): node for node in nodes if node.get("id") is not None}

    if not outputs:
        errors.append(
            StrategyValidationError(
                code="missing_output",
                message="전략 그래프는 하나 이상의 실행 출력을 가져야 한다.",
            )
        )

    for node in nodes:
        node_id = str(node.get("id", ""))
        node_type = str(node.get("type", ""))
        config = _mapping(node.get("config"))
        if node_type.startswith("dataset.") and config.get("missingDataPolicy") not in (
            _MISSING_DATA_POLICIES
        ):
            errors.append(
                StrategyValidationError(
                    code="missing_data_policy_required",
                    node_id=node_id,
                    message="dataset 입력 노드는 결측 정책을 명시해야 한다.",
                )
            )
        if _has_positive_lookahead(config):
            errors.append(
                StrategyValidationError(
                    code="look_ahead_detected",
                    node_id=node_id,
                    message="미래 값을 참조하는 look-ahead 설정은 허용하지 않는다.",
                )
            )
        if node_type == "indicator.sma":
            window = _int_config(config, "windowPeriods")
            warmup = _int_config(config, "warmupPeriods")
            if window is None or not 1 <= window <= 10_000:
                errors.append(
                    StrategyValidationError(
                        code="parameter_out_of_range",
                        node_id=node_id,
                        message="SMA windowPeriods는 1 이상 10000 이하여야 한다.",
                    )
                )
            if window is not None and warmup is not None and warmup < window:
                errors.append(
                    StrategyValidationError(
                        code="insufficient_warmup",
                        node_id=node_id,
                        message="warmupPeriods는 windowPeriods 이상이어야 한다.",
                    )
                )
        if node_type == "condition.greater_than":
            threshold = config.get("threshold")
            if threshold is not None and not _is_decimal_string_or_number(threshold):
                errors.append(
                    StrategyValidationError(
                        code="parameter_out_of_range",
                        node_id=node_id,
                        message="비교 임계값은 decimal 문자열 또는 숫자여야 한다.",
                    )
                )

    graph_edges: dict[str, set[str]] = defaultdict(set)
    indegree: dict[str, int] = {node_id: 0 for node_id in nodes_by_id}
    for index, edge in enumerate(edges):
        from_node_id = str(edge.get("from_node", ""))
        to_node_id = str(edge.get("to_node", ""))
        from_port = _find_port(nodes_by_id.get(from_node_id), "output_ports", edge.get("from_port"))
        to_port = _find_port(nodes_by_id.get(to_node_id), "input_ports", edge.get("to_port"))
        if from_port is None or to_port is None:
            errors.append(
                StrategyValidationError(
                    code="port_type_mismatch",
                    edge_index=index,
                    message="edge가 존재하지 않는 port를 참조한다.",
                )
            )
        else:
            from_type = from_port.get("dataType")
            to_type = to_port.get("dataType")
            if from_type != to_type:
                errors.append(
                    StrategyValidationError(
                        code="port_type_mismatch",
                        edge_index=index,
                        message="edge 양끝의 자료형이 다르다.",
                    )
                )
            from_timeframe = from_port.get("timeframe")
            to_timeframe = to_port.get("timeframe")
            if (
                from_timeframe is not None
                and to_timeframe is not None
                and from_timeframe != to_timeframe
            ):
                errors.append(
                    StrategyValidationError(
                        code="timeframe_incompatible",
                        edge_index=index,
                        message="edge 양끝의 시간 주기가 다르다.",
                    )
                )
        if (
            from_node_id in nodes_by_id
            and to_node_id in nodes_by_id
            and to_node_id not in graph_edges[from_node_id]
        ):
            graph_edges[from_node_id].add(to_node_id)
            indegree[to_node_id] = indegree.get(to_node_id, 0) + 1

    if _has_cycle(graph_edges, indegree):
        errors.append(
            StrategyValidationError(
                code="cycle_detected",
                message="전략 그래프는 순환을 가질 수 없다.",
            )
        )

    for output in outputs:
        node_id = str(output.get("node", ""))
        if _find_port(nodes_by_id.get(node_id), "output_ports", output.get("port")) is None:
            errors.append(
                StrategyValidationError(
                    code="missing_output",
                    node_id=node_id or None,
                    message="출력이 존재하지 않는 node 또는 port를 참조한다.",
                )
            )

    stable_errors = tuple(_deduplicate_errors(errors))
    return StrategyValidationResult(
        valid=not stable_errors,
        errors=stable_errors,
        graph_hash=graph_hash,
    )


def _canonical_graph(graph: Mapping[str, object]) -> dict[str, object]:
    nodes = sorted(
        (_canonical_node(node) for node in _mapping_list(graph.get("nodes"))),
        key=_node_key,
    )
    edges = sorted(
        (_canonical_edge(edge) for edge in _mapping_list(graph.get("edges"))),
        key=_edge_key,
    )
    outputs = sorted(
        (_canonical_output(output) for output in _mapping_list(graph.get("outputs"))),
        key=lambda item: (str(item["node"]), str(item["port"])),
    )
    return {
        "schema_version": str(graph.get("schema_version", graph.get("schemaVersion", ""))),
        "nodes": nodes,
        "edges": edges,
        "outputs": outputs,
    }


def _canonical_node(node: Mapping[str, object]) -> dict[str, object]:
    return {
        "id": str(node.get("id", "")),
        "type": str(node.get("type", "")),
        "config": _canonical_value(node.get("config", {})),
        "input_ports": sorted(
            (_canonical_port(port) for port in _mapping_list(node.get("input_ports"))),
            key=lambda item: str(item["name"]),
        ),
        "output_ports": sorted(
            (_canonical_port(port) for port in _mapping_list(node.get("output_ports"))),
            key=lambda item: str(item["name"]),
        ),
    }


def _canonical_edge(edge: Mapping[str, object]) -> dict[str, object]:
    return {
        "from_node": str(edge.get("from_node", "")),
        "from_port": str(edge.get("from_port", "")),
        "to_node": str(edge.get("to_node", "")),
        "to_port": str(edge.get("to_port", "")),
    }


def _canonical_output(output: Mapping[str, object]) -> dict[str, object]:
    return {"node": str(output.get("node", "")), "port": str(output.get("port", ""))}


def _canonical_port(port: Mapping[str, object]) -> dict[str, object]:
    timeframe = port.get("timeframe")
    if timeframe is not None and str(timeframe) not in _TIMEFRAMES:
        timeframe = str(timeframe)
    return {
        "name": str(port.get("name", "")),
        "dataType": str(port.get("dataType", "")),
        "timeframe": None if timeframe is None else str(timeframe),
    }


def _canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(
        _canonical_value(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _canonical_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_canonical_value(item) for item in value]
    if isinstance(value, Decimal):
        return format(value, "f")
    return value


def _mapping(value: object) -> Mapping[str, object]:
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else {}


def _mapping_list(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    return [cast(Mapping[str, object], item) for item in value if isinstance(item, Mapping)]


def _node_key(node: Mapping[str, object]) -> tuple[str, str]:
    return str(node["id"]), str(node["type"])


def _edge_key(edge: Mapping[str, object]) -> tuple[str, str, str, str]:
    return (
        str(edge["from_node"]),
        str(edge["from_port"]),
        str(edge["to_node"]),
        str(edge["to_port"]),
    )


def _find_port(
    node: Mapping[str, object] | None, port_key: str, port_name: object
) -> Mapping[str, object] | None:
    if node is None:
        return None
    for port in _mapping_list(node.get(port_key)):
        if port.get("name") == port_name:
            return port
    return None


def _int_config(config: Mapping[str, object], key: str) -> int | None:
    value = config.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _has_positive_lookahead(config: Mapping[str, object]) -> bool:
    for key in ("lookAheadPeriods", "futureOffsetPeriods"):
        value = _int_config(config, key)
        if value is not None and value > 0:
            return True
    offset = _int_config(config, "offsetPeriods")
    return offset is not None and offset < 0


def _is_decimal_string_or_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int | float | Decimal):
        return True
    if isinstance(value, str):
        try:
            Decimal(value)
        except InvalidOperation:
            return False
        return True
    return False


def _has_cycle(graph_edges: Mapping[str, set[str]], indegree: Mapping[str, int]) -> bool:
    remaining = dict(indegree)
    ready = deque(node for node, degree in remaining.items() if degree == 0)
    visited = 0
    while ready:
        node = ready.popleft()
        visited += 1
        for target in graph_edges.get(node, set()):
            remaining[target] -= 1
            if remaining[target] == 0:
                ready.append(target)
    return visited != len(remaining)


def _deduplicate_errors(
    errors: Sequence[StrategyValidationError],
) -> list[StrategyValidationError]:
    seen: set[tuple[str, str | None, int | None]] = set()
    stable: list[StrategyValidationError] = []
    for error in errors:
        if error.code not in _VALIDATION_CODES:
            continue
        key = (error.code, error.node_id, error.edge_index)
        if key not in seen:
            seen.add(key)
            stable.append(error)
    return stable
