from pathlib import Path
from typing import Any, cast

import yaml

DEFAULT_CATALOG_PATH = Path("docs/contracts/upbit/upbit-api-catalog.yaml")


def load_catalog(path: Path = DEFAULT_CATALOG_PATH) -> dict[str, Any]:
    """기계 검증 계약인 업비트 기능 카탈로그를 읽는다."""
    return cast(dict[str, Any], yaml.safe_load(path.read_text()))
