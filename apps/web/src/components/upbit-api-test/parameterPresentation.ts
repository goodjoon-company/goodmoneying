const COMMON_LABELS: Record<string, string> = {
  address: "주소",
  amount: "수량",
  cancel_side: "취소 방향",
  codes: "페어 코드 목록",
  converting_price_unit: "환산 통화",
  count: "조회 개수",
  currency: "통화",
  cursor: "커서",
  days_ago: "조회 일수",
  deposit_uuid: "입금 UUID",
  direction: "방향",
  end_time: "종료 시각",
  exclude_pairs: "제외 페어",
  from: "시작 값",
  identifier: "식별자",
  "identifiers[]": "식별자 목록",
  include_expired: "만료 항목 포함",
  is_details: "상세 정보 포함",
  is_only_realtime: "실시간 데이터만",
  is_only_snapshot: "스냅샷 데이터만",
  level: "호가 모아보기 단위",
  limit: "페이지 크기",
  market: "거래쌍",
  markets: "거래쌍 목록",
  method: "방식",
  net_type: "네트워크 유형",
  new_identifier: "새 주문 식별자",
  new_ord_type: "새 주문 유형",
  new_price: "새 주문 가격",
  new_smp_type: "새 자전거래 방지 유형",
  new_time_in_force: "새 주문 체결 조건",
  new_volume: "새 주문 수량",
  ord_type: "주문 유형",
  order_by: "정렬 순서",
  page: "페이지",
  pairs: "페어 목록",
  prev_order_identifier: "기존 주문 식별자",
  prev_order_uuid: "기존 주문 UUID",
  price: "가격",
  quote_currencies: "마켓 목록",
  secondary_address: "보조 주소",
  side: "주문 방향",
  smp_type: "자전거래 방지 유형",
  start_time: "시작 시각",
  state: "상태",
  "states[]": "상태 목록",
  states: "상태 목록",
  time_in_force: "주문 체결 조건",
  to: "종료 값",
  transaction_type: "거래 유형",
  two_factor_type: "2차 인증 방식",
  txid: "트랜잭션 ID",
  "txids[]": "트랜잭션 ID 목록",
  unit: "캔들 단위",
  uuid: "UUID",
  "uuids[]": "UUID 목록",
  vasp_uuid: "VASP UUID",
  volume: "수량"
};

const ENDPOINT_LABELS: Record<string, string> = {
  "rest.list-pair-trades:to": "조회 종료 시각",
  "rest.batch-cancel-orders:count": "취소 주문 수",
  "rest.post-universal-transfer:from": "출발 포켓",
  "rest.post-universal-transfer:to": "도착 포켓",
  "rest.post-transfer:to": "도착 포켓",
  "rest.get-universal-transfer:from": "출발 포켓",
  "rest.get-universal-transfer:to": "도착 포켓",
  "rest.get-transfer:to": "도착 포켓",
  "rest.list-withdrawals:from": "이전 커서",
  "rest.list-withdrawals:to": "다음 커서",
  "rest.list-deposits:from": "이전 커서",
  "rest.list-deposits:to": "다음 커서"
};

export function parameterDisplayName(endpointId: string, parameterName: string): string {
  const candleLabel = endpointId.startsWith("rest.list-candles-") && parameterName === "to"
    ? "조회 종료 시각"
    : undefined;
  const korean = ENDPOINT_LABELS[`${endpointId}:${parameterName}`]
    ?? candleLabel
    ?? COMMON_LABELS[parameterName]
    ?? "파라미터";
  return `${korean}(${parameterName})`;
}
