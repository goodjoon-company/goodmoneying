from __future__ import annotations

import base64
import hashlib
import re

UPBIT_LIVE_ORDER_IDENTIFIER_PATTERN = re.compile(r"^gm1_[a-z2-7]{52}$")


def derive_upbit_live_order_identifier(
    exchange_account_stable_id: str,
    idempotency_key: str,
) -> str:
    account = _non_blank(exchange_account_stable_id, "exchange_account_stable_id")
    key = _non_blank(idempotency_key, "idempotency_key")
    digest = hashlib.sha256(f"{account}:{key}".encode()).digest()
    encoded = base64.b32encode(digest).decode("ascii").lower().rstrip("=")
    identifier = f"gm1_{encoded}"
    if not is_upbit_live_order_identifier(identifier):
        raise ValueError("생성된 Upbit live 주문 identifier가 계약 형식과 다르다.")
    return identifier


def is_upbit_live_order_identifier(value: str) -> bool:
    return UPBIT_LIVE_ORDER_IDENTIFIER_PATTERN.fullmatch(value) is not None


def _non_blank(value: str, field_name: str) -> str:
    if not value:
        raise ValueError(f"{field_name} 값은 비어 있을 수 없다.")
    if value != value.strip():
        raise ValueError(f"{field_name} 값은 앞뒤 공백을 포함할 수 없다.")
    return value
