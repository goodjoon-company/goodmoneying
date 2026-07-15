import { describe, expect, it } from "vitest";

import {
  buildInitialParameters,
  coerceParameterValue,
  formatParameterValue,
  isCommonParameter,
  quotationGroups,
  selectQuotationEndpoints,
  serializeParameters
} from "./workbench";
import type { CatalogEndpoint } from "./types";

const endpoints: CatalogEndpoint[] = [
  {
    endpoint_id: "rest.list-trading-pairs",
    title: "페어 목록 조회",
    category: "quotation",
    functional_group: "pair",
    method: "GET",
    path: "/v1/market/all",
    parameters: [{ name: "is_details", location: "query", type: "boolean", required: false }],
    rate_limit_group: "market",
    safety: "read",
    source_url: "https://docs.upbit.com/kr/reference/list-trading-pairs"
  },
  {
    endpoint_id: "rest.list-candles-minutes",
    title: "분 캔들 조회",
    category: "quotation",
    functional_group: "candle",
    method: "GET",
    path: "/v1/candles/minutes/{unit}",
    parameters: [
      { name: "unit", location: "path", type: "integer", required: true, enum: [1, 3, 5] },
      { name: "market", location: "query", type: "string", required: true },
      { name: "to", location: "query", type: "string", required: false, format: "date-time" },
      { name: "count", location: "query", type: "integer", required: false, minimum: 1, maximum: 200 }
    ],
    rate_limit_group: "candle",
    safety: "read",
    source_url: "https://docs.upbit.com/kr/reference/list-candles-minutes"
  },
  {
    endpoint_id: "rest.list-orderbook-levels",
    title: "호가 모아보기 단위 조회",
    category: "quotation",
    functional_group: "orderbook",
    deprecated: true,
    method: "GET",
    path: "/v1/orderbook/supported_levels",
    parameters: [{ name: "markets", location: "query", type: "string", required: true }],
    rate_limit_group: "orderbook",
    safety: "read",
    source_url: "https://docs.upbit.com/kr/reference/list-orderbook-levels"
  }
];

describe("카탈로그 기반 작업대", () => {
  it("Quotation 엔드포인트를 기능 탭 순서로 분류하고 사용 중단 항목도 보존한다", () => {
    const selected = selectQuotationEndpoints(endpoints);

    expect(selected).toHaveLength(3);
    expect(quotationGroups.map((group) => group.id)).toEqual([
      "pair", "candle", "trade", "ticker", "orderbook"
    ]);
    expect(selected.at(-1)?.deprecated).toBe(true);
  });

  it("공통 페어·마켓·기준 자산을 필드에 전파하고 타입을 보존한다", () => {
    expect(buildInitialParameters(endpoints[1], {
      market: "KRW-BTC", quote: "KRW", base: "BTC"
    })).toEqual({ unit: 1, market: "KRW-BTC", count: 200 });
    expect(coerceParameterValue(endpoints[1].parameters[0], "5")).toBe(5);
    expect(coerceParameterValue(endpoints[0].parameters[0], "true")).toBe(true);
    expect(coerceParameterValue({
      name: "states", location: "query", type: "array", required: false
    }, "wait,done")).toEqual(["wait", "done"]);
    expect(coerceParameterValue({
      name: "to", location: "query", type: "string", required: false, format: "date-time"
    }, "2026-07-16T09:00")).toMatch(/^2026-07-16T00:00:00\.000Z$/);
    expect(formatParameterValue({
      name: "to", location: "query", type: "string", required: false, format: "date-time"
    }, "2026-07-16T00:00:00.000Z")).toBe("2026-07-16T09:00");
  });

  it("공통 파라미터는 동적 값 대신 최신 공통 조회 기준만 직렬화한다", () => {
    const context = { market: "BTC-ETH", quote: "BTC", base: "ETH" };

    expect(endpoints[1].parameters.filter((parameter) => isCommonParameter(parameter.name)))
      .toHaveLength(1);
    expect(serializeParameters(
      endpoints[1],
      { unit: 5, market: "KRW-BTC", count: 50 },
      context
    )).toEqual({ unit: 5, market: "BTC-ETH", count: 50 });
  });
});
