import { describe, expect, it } from "vitest";
import { parameterDisplayName } from "./parameterPresentation";

const currentParameterNames = [
  "address", "amount", "cancel_side", "codes", "converting_price_unit", "count",
  "currency", "cursor", "days_ago", "deposit_uuid", "direction", "end_time",
  "exclude_pairs", "from", "identifier", "identifiers[]", "include_expired", "is_details",
  "is_only_realtime", "is_only_snapshot", "level", "limit", "market", "markets", "method",
  "net_type", "new_identifier", "new_ord_type", "new_price", "new_smp_type",
  "new_time_in_force", "new_volume", "ord_type", "order_by", "page", "pairs",
  "prev_order_identifier", "prev_order_uuid", "price", "quote_currencies", "secondary_address",
  "side", "smp_type", "start_time", "state", "states[]", "time_in_force", "to",
  "transaction_type", "two_factor_type", "txid", "txids[]", "unit", "uuid", "uuids[]",
  "vasp_uuid", "volume"
];

describe("업비트 파라미터 표시 이름", () => {
  it("현재 카탈로그 이름을 모두 한글 이름과 원본 이름으로 병기한다", () => {
    for (const name of currentParameterNames) {
      const label = parameterDisplayName("", name);
      expect(label).toMatch(new RegExp(`\\(${name.replaceAll("[", "\\[").replaceAll("]", "\\]")}\\)$`));
      expect(label).not.toBe(name);
      expect(label.indexOf("("), name).toBeGreaterThan(0);
    }
  });

  it("엔드포인트 문맥에 맞게 같은 이름을 구체화한다", () => {
    expect(parameterDisplayName("rest.list-candles-minutes", "to"))
      .toBe("조회 종료 시각(to)");
    expect(parameterDisplayName("rest.list-trading-pairs", "is_details"))
      .toBe("상세 정보 포함(is_details)");
    expect(parameterDisplayName("rest.batch-cancel-orders", "count"))
      .toBe("취소 주문 수(count)");
    expect(parameterDisplayName("rest.post-universal-transfer", "from"))
      .toBe("출발 포켓(from)");
  });
});
