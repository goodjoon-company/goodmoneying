from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

_LIVE_IDENTIFIER = re.compile(r"^gm1_[a-z2-7]{52}$")
_FIELD_ORDER = (
    "market",
    "side",
    "volume",
    "price",
    "ord_type",
    "identifier",
    "time_in_force",
    "smp_type",
)
_ALLOWED_FIELDS = frozenset(_FIELD_ORDER)
_SIDE_VALUES = frozenset({"bid", "ask"})
_ORD_TYPE_VALUES = frozenset({"limit", "price", "market", "best"})
_TIME_IN_FORCE_VALUES = frozenset({"ioc", "fok", "post_only"})
_SMP_TYPE_VALUES = frozenset({"cancel_maker", "cancel_taker", "reduce"})


class InvalidUpbitOrderSubmitRehearsal(ValueError):
    """Upbit 주문 제출 리허설 입력이 공식 주문 생성 계약을 만족하지 않는다."""


@dataclass(frozen=True)
class UpbitOrderSubmitRehearsal:
    endpoint_key: str
    http_method: str
    request_path: str
    canonical_payload: dict[str, str]
    request_hash: str
    query_string: str
    query_hash: str
    rehearsed_at: datetime
    actual_request_sent: bool
    would_submit: bool
    can_bind_response: bool


def build_upbit_order_submit_rehearsal(
    request_payload: Mapping[str, object],
    *,
    rehearsed_at: datetime,
) -> UpbitOrderSubmitRehearsal:
    canonical_payload = _canonical_payload(request_payload)
    query_string = _query_string(canonical_payload)
    request_body = json.dumps(
        canonical_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return UpbitOrderSubmitRehearsal(
        endpoint_key="rest.new-order",
        http_method="POST",
        request_path="/v1/orders",
        canonical_payload=canonical_payload,
        request_hash=hashlib.sha256(request_body.encode("utf-8")).hexdigest(),
        query_string=query_string,
        query_hash=hashlib.sha512(query_string.encode("utf-8")).hexdigest(),
        rehearsed_at=rehearsed_at,
        actual_request_sent=False,
        would_submit=False,
        can_bind_response=False,
    )


def _canonical_payload(request_payload: Mapping[str, object]) -> dict[str, str]:
    if not isinstance(request_payload, Mapping):
        raise InvalidUpbitOrderSubmitRehearsal("request_payload는 객체여야 한다.")

    unknown_fields = sorted(set(request_payload) - _ALLOWED_FIELDS)
    if unknown_fields:
        raise InvalidUpbitOrderSubmitRehearsal(
            f"알 수 없는 주문 필드는 리허설할 수 없다: {', '.join(unknown_fields)}"
        )

    normalized: dict[str, str] = {}
    for field in _FIELD_ORDER:
        value = request_payload.get(field)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            raise InvalidUpbitOrderSubmitRehearsal(
                f"{field} 값은 비어 있지 않은 문자열이어야 한다."
            )
        normalized[field] = value.strip()

    _require(normalized, "market")
    _require(normalized, "side")
    _require(normalized, "ord_type")
    _require(normalized, "identifier")
    _enum(normalized["side"], _SIDE_VALUES, "side")
    _enum(normalized["ord_type"], _ORD_TYPE_VALUES, "ord_type")
    if "time_in_force" in normalized:
        _enum(normalized["time_in_force"], _TIME_IN_FORCE_VALUES, "time_in_force")
    if "smp_type" in normalized:
        _enum(normalized["smp_type"], _SMP_TYPE_VALUES, "smp_type")
    if _LIVE_IDENTIFIER.fullmatch(normalized["identifier"]) is None:
        raise InvalidUpbitOrderSubmitRehearsal(
            "identifier는 내부 gm1_ live 주문 식별자여야 한다."
        )
    if (
        normalized.get("time_in_force") == "post_only"
        and normalized.get("smp_type") is not None
    ):
        raise InvalidUpbitOrderSubmitRehearsal(
            "post_only 주문은 smp_type과 함께 리허설할 수 없다."
        )

    _validate_order_shape(normalized)
    return normalized


def _validate_order_shape(payload: Mapping[str, str]) -> None:
    ord_type = payload["ord_type"]
    side = payload["side"]
    has_price = "price" in payload
    has_volume = "volume" in payload

    if ord_type in {"price", "market"} and "time_in_force" in payload:
        raise InvalidUpbitOrderSubmitRehearsal(
            "시장가 주문은 time_in_force와 함께 리허설할 수 없다."
        )
    if ord_type == "limit" and not (has_price and has_volume):
        raise InvalidUpbitOrderSubmitRehearsal("limit 주문은 price와 volume이 필요하다.")
    if ord_type == "price" and not (side == "bid" and has_price and not has_volume):
        raise InvalidUpbitOrderSubmitRehearsal(
            "시장가 매수(price) 주문은 bid, price만 필요하다."
        )
    if ord_type == "market" and not (side == "ask" and has_volume and not has_price):
        raise InvalidUpbitOrderSubmitRehearsal(
            "시장가 매도(market) 주문은 ask, volume만 필요하다."
        )
    if ord_type == "best":
        if payload.get("time_in_force") not in {"ioc", "fok"}:
            raise InvalidUpbitOrderSubmitRehearsal(
                "최유리 지정가(best) 주문은 ioc 또는 fok가 필요하다."
            )
        if side == "bid" and not (has_price and not has_volume):
            raise InvalidUpbitOrderSubmitRehearsal(
                "최유리 매수(best bid) 주문은 price만 필요하다."
            )
        if side == "ask" and not (has_volume and not has_price):
            raise InvalidUpbitOrderSubmitRehearsal(
                "최유리 매도(best ask) 주문은 volume만 필요하다."
            )


def _query_string(canonical_payload: Mapping[str, str]) -> str:
    return "&".join(
        f"{field}={canonical_payload[field]}"
        for field in _FIELD_ORDER
        if field in canonical_payload
    )


def _require(payload: Mapping[str, str], field: str) -> None:
    if field not in payload:
        raise InvalidUpbitOrderSubmitRehearsal(f"{field} 값이 필요하다.")


def _enum(value: str, allowed: frozenset[str], field: str) -> None:
    if value not in allowed:
        raise InvalidUpbitOrderSubmitRehearsal(f"{field} 값이 허용되지 않는다.")
