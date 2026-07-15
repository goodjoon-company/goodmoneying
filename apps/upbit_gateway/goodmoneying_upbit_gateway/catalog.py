from pathlib import Path
from typing import Any, cast

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CATALOG_PATH = REPOSITORY_ROOT / "docs/contracts/upbit/upbit-api-catalog.yaml"


def load_catalog(path: Path = DEFAULT_CATALOG_PATH) -> dict[str, Any]:
    """기계 검증 계약인 업비트 기능 카탈로그를 읽는다."""
    return cast(dict[str, Any], yaml.safe_load(path.read_text()))


def endpoint_by_id(catalog: dict[str, Any], endpoint_id: str) -> dict[str, Any] | None:
    """REST·WebSocket 데이터·운용 카탈로그에서 endpoint_id를 찾는다."""
    for collection_name in ("rest_endpoints", "websocket_streams", "websocket_operations"):
        for endpoint in cast(list[dict[str, Any]], catalog[collection_name]):
            if endpoint["endpoint_id"] == endpoint_id:
                return endpoint
    return None
