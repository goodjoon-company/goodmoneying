from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, cast

from goodmoneying_shared.upbit_rest_reconciliation import (
    InvalidUpbitRestOrderSnapshot,
    build_upbit_rest_reconciliation_request,
    parse_upbit_rest_order_snapshot,
    plan_upbit_rest_snapshot_reconciliation,
)

Row = dict[str, object]


class InvalidUpbitLiveReconciliationApplication(ValueError):
    """Upbit live 주문 대사 적용 후보가 안전 경계를 만족하지 않는다."""


class LiveReconciliationStore(Protocol):
    def apply_upbit_live_reconciliation_application(
        self, **arguments: object
    ) -> Row: ...


@dataclass(frozen=True)
class UpbitLiveBindingSnapshot:
    id: int
    exchange_account_id: int
    order_intent_id: int
    exchange_order_id: int
    upbit_order_uuid: str
    upbit_identifier: str


def apply_upbit_live_rest_order_snapshot(
    store: LiveReconciliationStore,
    *,
    live_binding: Mapping[str, object],
    exchange_order_id: object,
    run_key: object,
    actor_id: object,
    reason: object,
    snapshot_payload: Mapping[str, object],
    knowledge_at: datetime,
    source_endpoint: str,
) -> Row:
    binding = _binding(live_binding)
    target_exchange_order_id = int(cast(int, exchange_order_id))
    if binding.exchange_order_id != target_exchange_order_id:
        raise InvalidUpbitLiveReconciliationApplication(
            "live binding의 exchange order와 적용 대상이 다르다."
        )
    snapshot = parse_upbit_rest_order_snapshot(
        snapshot_payload,
        knowledge_at=knowledge_at,
        source_endpoint=source_endpoint,
    )
    _validate_snapshot_matches_binding(snapshot.uuid, snapshot.identifier, binding)
    plan = plan_upbit_rest_snapshot_reconciliation(snapshot)
    if plan.ledger_action == "observe_only":
        return {
            "status": "observed",
            "exchangeOrderId": target_exchange_order_id,
            "observedStatus": plan.observed_status,
            "canResubmit": False,
            "sourceEndpoint": snapshot.source_endpoint,
            "liveBindingMatched": True,
        }

    request = build_upbit_rest_reconciliation_request(snapshot)
    result = store.apply_upbit_live_reconciliation_application(
        exchange_account_id=binding.exchange_account_id,
        order_intent_id=binding.order_intent_id,
        exchange_order_id=target_exchange_order_id,
        live_exchange_order_binding_id=binding.id,
        run_key=run_key,
        observed_status=request["observed_status"],
        fills=request["fills"],
        reconciliation_evidence=request["evidence"],
        source="rest_order_snapshot",
        source_endpoint=snapshot.source_endpoint,
        observed_upbit_order_uuid=snapshot.uuid,
        observed_upbit_identifier=snapshot.identifier,
        observed_state=snapshot.state,
        applied_at=knowledge_at,
        can_resubmit=False,
        actual_request_sent=False,
        actual_order_cancel_sent=False,
        application_evidence={
            "source": "upbit-live-rest-reconciliation-application",
            "restSnapshotSource": "upbit-rest-order-snapshot",
            "runKey": run_key,
            "observedStatus": request["observed_status"],
            "tradesCount": snapshot.trades_count,
            "canResubmit": False,
            "liveBindingMatched": True,
        },
        actor_id=actor_id,
        reason=reason,
        request_id=f"{run_key}:live-reconciliation-application",
        idempotency_key=f"{run_key}:live-reconciliation-application",
    )
    return {
        **result,
        "canResubmit": False,
        "actualRequestSent": False,
        "actualOrderCancelSent": False,
        "liveBindingMatched": True,
    }


def _validate_snapshot_matches_binding(
    order_uuid: str,
    identifier: str | None,
    binding: UpbitLiveBindingSnapshot,
) -> None:
    if order_uuid != binding.upbit_order_uuid:
        raise InvalidUpbitLiveReconciliationApplication(
            "REST snapshot UUID가 live binding UUID와 다르다."
        )
    if identifier != binding.upbit_identifier:
        raise InvalidUpbitLiveReconciliationApplication(
            "REST snapshot identifier가 live binding identifier와 다르다."
        )


def _binding(payload: Mapping[str, object]) -> UpbitLiveBindingSnapshot:
    try:
        return UpbitLiveBindingSnapshot(
            id=_int(_field(payload, "id", "liveBindingId")),
            exchange_account_id=_int(
                _field(payload, "exchange_account_id", "exchangeAccountId")
            ),
            order_intent_id=_int(_field(payload, "order_intent_id", "orderIntentId")),
            exchange_order_id=_int(
                _field(payload, "exchange_order_id", "exchangeOrderId")
            ),
            upbit_order_uuid=_str(_field(payload, "upbit_order_uuid", "upbitOrderUuid")),
            upbit_identifier=_str(_field(payload, "upbit_identifier", "identifier")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidUpbitLiveReconciliationApplication(
            "live binding snapshot 필드가 부족하거나 잘못됐다."
        ) from exc


def _field(payload: Mapping[str, object], snake: str, camel: str) -> object:
    if snake in payload:
        return payload[snake]
    if camel in payload:
        return payload[camel]
    raise KeyError(snake)


def _int(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("bool은 정수 식별자가 아니다.")
    return int(cast(int, value))


def _str(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("문자열 식별자가 비어 있다.")
    return value


__all__ = [
    "InvalidUpbitLiveReconciliationApplication",
    "InvalidUpbitRestOrderSnapshot",
    "LiveReconciliationStore",
    "UpbitLiveBindingSnapshot",
    "apply_upbit_live_rest_order_snapshot",
]
