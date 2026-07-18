from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal, Protocol, cast

Row = dict[str, object]
ALLOWED_SOURCE_ENDPOINTS = frozenset(
    {
        "GET /v1/order",
        "GET /v1/orders/open",
        "GET /v1/orders/closed",
        "GET /v1/orders/uuids",
    }
)

UpbitRestOrderState = Literal[
    "wait",
    "watch",
    "trade",
    "done",
    "cancel",
    "prevented",
    "rejected",
]
UpbitRestObservedStatus = Literal[
    "working",
    "partial_fill",
    "done",
    "cancel",
    "prevented",
    "rejected",
]
UpbitRestLedgerAction = Literal["apply", "observe_only"]
UpbitSmpType = Literal["reduce", "cancel_maker", "cancel_taker"]


class InvalidUpbitRestOrderSnapshot(ValueError):
    """Upbit REST 주문 snapshot이 내부 대사 입력 계약을 만족하지 않는다."""


class ReconciliationStore(Protocol):
    def reconcile_exchange_order(self, **arguments: object) -> Row: ...


@dataclass(frozen=True)
class UpbitRestTradeSnapshot:
    uuid: str | None
    side: Literal["buy", "sell"]
    price: Decimal
    volume: Decimal
    funds: Decimal | None
    occurred_at: datetime
    raw: dict[str, object]


@dataclass(frozen=True)
class UpbitRestOrderSnapshot:
    market: str
    uuid: str
    identifier: str | None
    side: Literal["buy", "sell"]
    state: UpbitRestOrderState
    created_at: datetime | None
    remaining_volume: Decimal | None
    executed_volume: Decimal | None
    paid_fee: Decimal
    smp_type: UpbitSmpType | None
    prevented_volume: Decimal
    prevented_locked: Decimal
    trades_count: int
    trades: tuple[UpbitRestTradeSnapshot, ...]
    knowledge_at: datetime
    source_endpoint: str
    raw: dict[str, object]


@dataclass(frozen=True)
class UpbitRestReconciliationPlan:
    observed_status: UpbitRestObservedStatus
    ledger_action: UpbitRestLedgerAction
    can_resubmit: bool
    reason: str


def parse_upbit_rest_order_snapshot(
    payload: Mapping[str, object],
    *,
    knowledge_at: datetime,
    source_endpoint: str,
) -> UpbitRestOrderSnapshot:
    endpoint = _source_endpoint(source_endpoint)
    fallback_side = _side(payload.get("side"))
    fallback_occurred_at = _optional_datetime(payload.get("created_at"))
    raw_trades = _trade_payloads(payload.get("trades"))
    trades = tuple(
        sorted(
            [
                _trade_snapshot(
                    trade,
                    fallback_side=fallback_side,
                    fallback_occurred_at=fallback_occurred_at,
                )
                for trade in raw_trades
            ],
            key=lambda trade: (
                trade.occurred_at,
                trade.uuid or "",
                trade.price,
                trade.volume,
            ),
        )
    )
    trades_count = _non_negative_int(
        payload.get("trades_count", len(trades)),
        "trades_count",
    )
    if trades_count != len(trades):
        raise InvalidUpbitRestOrderSnapshot(
            "Upbit REST trades_count와 trades 개수가 다르다."
        )
    snapshot = UpbitRestOrderSnapshot(
        market=_non_blank(payload.get("market"), "market"),
        uuid=_non_blank(payload.get("uuid"), "uuid"),
        identifier=_optional_str(payload.get("identifier")),
        side=fallback_side,
        state=_state(payload.get("state")),
        created_at=fallback_occurred_at,
        remaining_volume=_optional_decimal(payload.get("remaining_volume")),
        executed_volume=_optional_decimal(payload.get("executed_volume")),
        paid_fee=_optional_decimal(payload.get("paid_fee")) or Decimal("0"),
        smp_type=_optional_smp_type(payload.get("smp_type")),
        prevented_volume=_optional_decimal(payload.get("prevented_volume")) or Decimal("0"),
        prevented_locked=_optional_decimal(payload.get("prevented_locked")) or Decimal("0"),
        trades_count=trades_count,
        trades=trades,
        knowledge_at=_aware_utc(knowledge_at, "knowledge_at"),
        source_endpoint=endpoint,
        raw=dict(payload),
    )
    if snapshot.state in {"done", "cancel"} and (
        snapshot.executed_volume or Decimal("0")
    ) > 0 and not snapshot.trades:
        raise InvalidUpbitRestOrderSnapshot(
            "체결량이 있는 terminal REST snapshot은 trades를 포함해야 한다."
        )
    return snapshot


def plan_upbit_rest_snapshot_reconciliation(
    snapshot: UpbitRestOrderSnapshot,
) -> UpbitRestReconciliationPlan:
    observed_status = _observed_status(snapshot)
    ledger_action: UpbitRestLedgerAction = (
        "apply"
        if observed_status in {"done", "cancel", "prevented", "rejected"}
        else "observe_only"
    )
    return UpbitRestReconciliationPlan(
        observed_status=observed_status,
        ledger_action=ledger_action,
        can_resubmit=False,
        reason=(
            "upbit_rest_terminal_snapshot"
            if ledger_action == "apply"
            else "upbit_rest_working_snapshot_observe_only"
        ),
    )


def build_upbit_rest_reconciliation_request(
    snapshot: UpbitRestOrderSnapshot,
) -> Row:
    plan = plan_upbit_rest_snapshot_reconciliation(snapshot)
    if plan.ledger_action != "apply":
        raise InvalidUpbitRestOrderSnapshot(
            "진행 중 REST snapshot은 기존 terminal 대사 원장에 적용하지 않는다."
        )
    fills = _reconciliation_fills(snapshot)
    return {
        "observed_status": plan.observed_status,
        "fills": fills,
        "evidence": {
            "source": "upbit-rest-order-snapshot",
            "sourceEndpoint": snapshot.source_endpoint,
            "market": snapshot.market,
            "orderUuid": snapshot.uuid,
            "identifier": snapshot.identifier,
            "state": snapshot.state,
            "paidFee": str(snapshot.paid_fee),
            "smpType": snapshot.smp_type,
            "preventedVolume": str(snapshot.prevented_volume),
            "preventedLocked": str(snapshot.prevented_locked),
            "tradesCount": snapshot.trades_count,
            "canResubmit": False,
        },
    }


def apply_upbit_rest_order_snapshot(
    store: ReconciliationStore,
    *,
    exchange_order_id: object,
    run_key: object,
    actor_id: object,
    reason: object,
    snapshot_payload: Mapping[str, object],
    knowledge_at: datetime,
    source_endpoint: str,
) -> Row:
    snapshot = parse_upbit_rest_order_snapshot(
        snapshot_payload,
        knowledge_at=knowledge_at,
        source_endpoint=source_endpoint,
    )
    plan = plan_upbit_rest_snapshot_reconciliation(snapshot)
    if plan.ledger_action == "observe_only":
        return {
            "status": "observed",
            "exchangeOrderId": int(cast(int, exchange_order_id)),
            "observedStatus": plan.observed_status,
            "canResubmit": False,
            "sourceEndpoint": snapshot.source_endpoint,
        }
    request = build_upbit_rest_reconciliation_request(snapshot)
    result = store.reconcile_exchange_order(
        exchange_order_id=exchange_order_id,
        run_key=run_key,
        actor_id=actor_id,
        reason=reason,
        observed_status=request["observed_status"],
        fills=request["fills"],
        evidence=request["evidence"],
    )
    return {**result, "canResubmit": False}


def _observed_status(snapshot: UpbitRestOrderSnapshot) -> UpbitRestObservedStatus:
    if snapshot.state in {"wait", "watch"}:
        return "working"
    if snapshot.state == "trade":
        if (snapshot.remaining_volume or Decimal("0")) > 0:
            return "partial_fill"
        return "working"
    if snapshot.state in {"done", "cancel", "prevented", "rejected"}:
        return cast(UpbitRestObservedStatus, snapshot.state)
    raise InvalidUpbitRestOrderSnapshot(
        f"지원하지 않는 Upbit REST 주문 상태: {snapshot.state}"
    )


def _reconciliation_fills(snapshot: UpbitRestOrderSnapshot) -> list[Row]:
    fees = _distributed_fees(snapshot)
    fills: list[Row] = []
    for index, trade in enumerate(snapshot.trades, start=1):
        fills.append(
            {
                "fillSequence": index,
                "side": trade.side,
                "filledQuantity": trade.volume,
                "fillPrice": trade.price,
                "feePaid": fees[index - 1],
                "occurredAt": trade.occurred_at,
                "knowledgeAt": snapshot.knowledge_at,
                "evidence": {
                    "source": "upbit-rest-order-snapshot",
                    "sourceEndpoint": snapshot.source_endpoint,
                    "orderUuid": snapshot.uuid,
                    "identifier": snapshot.identifier,
                    "tradeUuid": trade.uuid,
                    "market": snapshot.market,
                    "state": snapshot.state,
                    "paidFee": str(snapshot.paid_fee),
                    "smpType": snapshot.smp_type,
                    "preventedVolume": str(snapshot.prevented_volume),
                    "preventedLocked": str(snapshot.prevented_locked),
                    "tradesCount": snapshot.trades_count,
                },
            }
        )
    return fills


def _distributed_fees(snapshot: UpbitRestOrderSnapshot) -> list[Decimal]:
    if not snapshot.trades:
        return []
    if snapshot.paid_fee == 0:
        return [Decimal("0") for _ in snapshot.trades]
    if len(snapshot.trades) == 1:
        return [snapshot.paid_fee]
    funds = [trade.funds for trade in snapshot.trades]
    if any(fund is None for fund in funds):
        raise InvalidUpbitRestOrderSnapshot(
            "다중 체결 fee 분배에는 각 trade funds가 필요하다."
        )
    total_funds = sum(cast(Decimal, fund) for fund in funds)
    if total_funds <= 0:
        raise InvalidUpbitRestOrderSnapshot("다중 체결 funds 합계는 0보다 커야 한다.")
    distributed: list[Decimal] = []
    assigned = Decimal("0")
    for index, fund in enumerate(funds):
        if index == len(funds) - 1:
            fee = snapshot.paid_fee - assigned
        else:
            fee = snapshot.paid_fee * cast(Decimal, fund) / total_funds
            assigned += fee
        distributed.append(fee)
    return distributed


def _trade_snapshot(
    payload: Mapping[str, object],
    *,
    fallback_side: Literal["buy", "sell"],
    fallback_occurred_at: datetime | None,
) -> UpbitRestTradeSnapshot:
    occurred_at = _optional_datetime(payload.get("created_at")) or fallback_occurred_at
    if occurred_at is None:
        raise InvalidUpbitRestOrderSnapshot("REST trade created_at이 없다.")
    return UpbitRestTradeSnapshot(
        uuid=_optional_str(payload.get("uuid")),
        side=_side(payload.get("side")) if payload.get("side") is not None else fallback_side,
        price=_positive_decimal(payload.get("price"), "trades.price"),
        volume=_positive_decimal(payload.get("volume"), "trades.volume"),
        funds=_optional_decimal(payload.get("funds")),
        occurred_at=occurred_at,
        raw=dict(payload),
    )


def _trade_payloads(value: object) -> list[Mapping[str, object]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise InvalidUpbitRestOrderSnapshot("REST trades 필드는 list여야 한다.")
    trades: list[Mapping[str, object]] = []
    for trade in value:
        if not isinstance(trade, Mapping):
            raise InvalidUpbitRestOrderSnapshot("REST trades 원소는 object여야 한다.")
        trades.append(cast(Mapping[str, object], trade))
    return trades


def _state(value: object) -> UpbitRestOrderState:
    if not isinstance(value, str):
        raise InvalidUpbitRestOrderSnapshot("REST order state는 문자열이어야 한다.")
    if value not in {"wait", "watch", "trade", "done", "cancel", "prevented", "rejected"}:
        raise InvalidUpbitRestOrderSnapshot(f"지원하지 않는 REST order state: {value}")
    return cast(UpbitRestOrderState, value)


def _optional_smp_type(value: object) -> UpbitSmpType | None:
    if value is None:
        return None
    if value not in {"reduce", "cancel_maker", "cancel_taker"}:
        raise InvalidUpbitRestOrderSnapshot(
            "REST smp_type은 reduce, cancel_maker, cancel_taker 중 하나여야 한다."
        )
    return cast(UpbitSmpType, value)


def _source_endpoint(value: object) -> str:
    endpoint = _non_blank(value, "source_endpoint")
    if endpoint not in ALLOWED_SOURCE_ENDPOINTS:
        raise InvalidUpbitRestOrderSnapshot("허용되지 않은 Upbit 주문조회 endpoint다.")
    return endpoint


def _side(value: object) -> Literal["buy", "sell"]:
    if value == "bid":
        return "buy"
    if value == "ask":
        return "sell"
    raise InvalidUpbitRestOrderSnapshot("REST order side는 bid 또는 ask여야 한다.")


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value == "":
        raise InvalidUpbitRestOrderSnapshot("REST 문자열 필드가 비어 있거나 문자열이 아니다.")
    return value


def _non_blank(value: object, field_name: str) -> str:
    if not isinstance(value, str) or value == "":
        raise InvalidUpbitRestOrderSnapshot(f"{field_name}은 비어 있지 않은 문자열이어야 한다.")
    return value


def _optional_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise InvalidUpbitRestOrderSnapshot(
            "REST 숫자 필드가 decimal로 변환되지 않는다."
        ) from exc
    if decimal < 0:
        raise InvalidUpbitRestOrderSnapshot("REST 숫자 필드는 음수일 수 없다.")
    return decimal


def _positive_decimal(value: object, field_name: str) -> Decimal:
    decimal = _optional_decimal(value)
    if decimal is None or decimal <= 0:
        raise InvalidUpbitRestOrderSnapshot(f"{field_name}은 0보다 커야 한다.")
    return decimal


def _non_negative_int(value: object, field_name: str) -> int:
    if isinstance(value, bool):
        raise InvalidUpbitRestOrderSnapshot(f"{field_name}은 정수여야 한다.")
    try:
        integer = int(cast(int, value))
    except (TypeError, ValueError) as exc:
        raise InvalidUpbitRestOrderSnapshot(f"{field_name}은 정수여야 한다.") from exc
    if integer < 0:
        raise InvalidUpbitRestOrderSnapshot(f"{field_name}은 음수일 수 없다.")
    return integer


def _optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _aware_utc(value, "datetime")
    if not isinstance(value, str) or value == "":
        raise InvalidUpbitRestOrderSnapshot("REST datetime 필드가 문자열이 아니다.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidUpbitRestOrderSnapshot(
            "REST datetime 필드가 ISO 8601이 아니다."
        ) from exc
    return _aware_utc(parsed, "datetime")


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None:
        raise InvalidUpbitRestOrderSnapshot(f"{field_name}은 timezone이 필요하다.")
    return value.astimezone(UTC)
