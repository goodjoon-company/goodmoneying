from importlib.resources import files
from pathlib import Path
from typing import Any, cast

import yaml

PACKAGED_CATALOG = files("goodmoneying_upbit_gateway").joinpath(
    "data/upbit-api-catalog.yaml"
)


def load_catalog(path: Path | None = None) -> dict[str, Any]:
    """기계 검증 계약인 업비트 기능 카탈로그를 읽는다."""
    text = (
        path.read_text(encoding="utf-8")
        if path is not None
        else PACKAGED_CATALOG.read_text(encoding="utf-8")
    )
    return cast(dict[str, Any], yaml.safe_load(text))


def endpoint_by_id(catalog: dict[str, Any], endpoint_id: str) -> dict[str, Any] | None:
    """REST·WebSocket 데이터·운용 카탈로그에서 endpoint_id를 찾는다."""
    for collection_name in ("rest_endpoints", "websocket_streams", "websocket_operations"):
        for endpoint in cast(list[dict[str, Any]], catalog[collection_name]):
            if endpoint["endpoint_id"] == endpoint_id:
                return endpoint
    return None
