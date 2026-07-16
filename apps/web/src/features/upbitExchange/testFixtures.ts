import type {
  ExchangeCatalog,
  ExchangeCatalogEndpoint,
  ExchangeGateway,
  GatewayHealth,
  TraceEnvelope
} from "./types";

type EndpointSeed = readonly [
  endpointId: string,
  group: ExchangeCatalogEndpoint["functional_group"],
  safety: ExchangeCatalogEndpoint["safety"]
];

const endpointSeeds: EndpointSeed[] = [
  ["rest.get-pocket-information", "pocket", "read"],
  ["rest.get-pocket-api-keys", "pocket", "read"],
  ["rest.get-sub-pocket-balance", "pocket", "read"],
  ["rest.post-universal-transfer", "pocket", "blocked"],
  ["rest.get-universal-transfer", "pocket", "read"],
  ["rest.post-transfer", "pocket", "blocked"],
  ["rest.get-transfer", "pocket", "read"],
  ["rest.get-balance", "asset", "read"],
  ["rest.available-order-information", "order", "read"],
  ["rest.new-order", "order", "blocked"],
  ["rest.order-test", "order", "test"],
  ["rest.get-order", "order", "read"],
  ["rest.list-orders-by-ids", "order", "read"],
  ["rest.list-open-orders", "order", "read"],
  ["rest.list-closed-orders", "order", "read"],
  ["rest.cancel-order", "order", "blocked"],
  ["rest.cancel-orders-by-ids", "order", "blocked"],
  ["rest.batch-cancel-orders", "order", "blocked"],
  ["rest.cancel-and-new-order", "order", "blocked"],
  ["rest.available-withdrawal-information", "withdrawal", "read"],
  ["rest.list-withdrawal-addresses", "withdrawal", "read"],
  ["rest.withdraw", "withdrawal", "blocked"],
  ["rest.withdraw-krw", "withdrawal", "blocked"],
  ["rest.get-withdrawal", "withdrawal", "read"],
  ["rest.list-withdrawals", "withdrawal", "read"],
  ["rest.cancel-withdrawal", "withdrawal", "blocked"],
  ["rest.available-deposit-information", "deposit", "read"],
  ["rest.create-deposit-address", "deposit", "blocked"],
  ["rest.get-deposit-address", "deposit", "read"],
  ["rest.list-deposit-addresses", "deposit", "read"],
  ["rest.deposit-krw", "deposit", "blocked"],
  ["rest.get-deposit", "deposit", "read"],
  ["rest.list-deposits", "deposit", "read"],
  ["rest.list-travelrule-vasps", "travel_rule", "read"],
  ["rest.verify-travelrule-by-uuid", "travel_rule", "blocked"],
  ["rest.verify-travelrule-by-txid", "travel_rule", "blocked"],
  ["rest.get-service-status", "service", "read"],
  ["rest.list-api-keys", "service", "read"]
];

const titleOverrides: Record<string, string> = {
  "rest.get-pocket-information": "포켓 정보 조회",
  "rest.get-pocket-api-keys": "포켓별 API Key 목록 조회",
  "rest.get-sub-pocket-balance": "서브포켓 잔고 조회",
  "rest.get-universal-transfer": "메인포켓 자산 이전 목록 조회",
  "rest.get-balance": "포켓 잔고 조회",
  "rest.available-order-information": "페어별 주문 가능 정보 조회",
  "rest.order-test": "주문 생성 테스트",
  "rest.new-order": "주문 생성",
  "rest.get-order": "개별 주문 조회",
  "rest.cancel-order": "개별 주문 취소 접수",
  "rest.list-open-orders": "체결 대기 주문 목록 조회",
  "rest.get-withdrawal": "개별 출금 조회",
  "rest.get-deposit": "개별 입금 조회",
  "rest.get-service-status": "입출금 서비스 상태 조회"
};

const parametersByEndpoint: Record<string, ExchangeCatalogEndpoint["parameters"]> = {
  "rest.get-pocket-api-keys": [
    { name: "uuids[]", location: "query", type: "array", items: "string", required: false },
    { name: "include_expired", location: "query", type: "boolean", required: false }
  ],
  "rest.available-order-information": [
    { name: "market", location: "query", type: "string", required: true }
  ],
  "rest.order-test": orderParameters(),
  "rest.new-order": orderParameters(),
  "rest.get-order": identifierParameters("identifier"),
  "rest.cancel-order": identifierParameters("identifier"),
  "rest.list-open-orders": [
    { name: "market", location: "query", type: "string", required: false },
    { name: "limit", location: "query", type: "integer", required: false, default: 100, minimum: 1, maximum: 100, step: 1, unit: "개" }
  ],
  "rest.get-universal-transfer": [
    { name: "start_time", location: "query", type: "string", format: "date-time", timezone: "Asia/Seoul", step: 1, required: false },
    { name: "order_by", location: "query", type: "string", enum: ["asc", "desc"], required: false }
  ],
  "rest.withdraw": [
    { name: "currency", location: "body", type: "string", required: true },
    { name: "amount", location: "body", type: "string", required: true },
    { name: "address", location: "body", type: "string", required: true }
  ],
  "rest.get-withdrawal": identifierParameters("txid", true),
  "rest.list-withdrawals": [
    { name: "limit", location: "query", type: "integer", required: false, default: 100, maximum: 100, step: 1, unit: "개" },
    { name: "from", location: "query", type: "string", format: "cursor", required: false },
    { name: "to", location: "query", type: "string", format: "cursor", required: false }
  ],
  "rest.get-deposit": [
    { name: "currency", location: "query", type: "string", required: false },
    { name: "uuid", location: "query", type: "string", required: false },
    { name: "txid", location: "query", type: "string", required: false }
  ]
};

const anyOfRequiredByEndpoint: Record<string, string[][]> = {
  "rest.get-order": [["uuid"], ["identifier"]],
  "rest.cancel-order": [["uuid"], ["identifier"]],
  "rest.get-withdrawal": [["uuid"], ["txid"]],
  "rest.get-deposit": [["uuid"], ["txid", "currency"]]
};

export const exchangeCatalogFixture: ExchangeCatalog = {
  catalog_version: "1.6.3",
  verified_at: "2026-07-16",
  rest_endpoints: endpointSeeds.map(([endpointId, functionalGroup, safety]) => ({
    endpoint_id: endpointId,
    title: titleOverrides[endpointId] ?? endpointId.replace("rest.", ""),
    category: "exchange",
    functional_group: functionalGroup,
    method: safety === "blocked" || safety === "test" ? "POST" : "GET",
    path: `/v1/${endpointId.replace("rest.", "").replaceAll("-", "/")}`,
    parameters: parametersByEndpoint[endpointId] ?? [],
    ...(anyOfRequiredByEndpoint[endpointId]
      ? { any_of_required: anyOfRequiredByEndpoint[endpointId] }
      : {}),
    rate_limit_group: safety === "test" ? "order-test" : "default",
    safety,
    source_url: `https://docs.upbit.com/kr/reference/${endpointId.replace("rest.", "")}.md`
  }))
};

export function traceFor(
  endpointId: string,
  body: unknown,
  statusCode = 200
): TraceEnvelope {
  return {
    trace_id: "00000000-0000-4000-8000-000000000022",
    endpoint_id: endpointId,
    request: { method: "GET", path: "/v1/accounts", parameters: {} },
    response: { status_code: statusCode, body },
    rate_limit: { group: "default", remaining_sec: 29, retry_after: null },
    duration_ms: 12.5,
    received_at: "2026-07-16T12:00:00Z"
  };
}

export function fakeGateway({
  health = { status: "ok", service: "upbit-gateway", catalog_version: "1.6.3", credentials_configured: true },
  execute = async ({ endpoint_id }: { endpoint_id: string; parameters: Record<string, unknown> }) =>
    traceFor(endpoint_id, [])
}: {
  health?: GatewayHealth;
  execute?: ExchangeGateway["execute"];
} = {}): ExchangeGateway {
  return {
    getHealth: async () => health,
    getCatalog: async () => exchangeCatalogFixture,
    execute
  };
}

function orderParameters(): ExchangeCatalogEndpoint["parameters"] {
  return [
    { name: "market", location: "body", type: "string", required: true },
    { name: "side", location: "body", type: "string", required: true, enum: ["ask", "bid"] },
    { name: "volume", location: "body", type: "string", required: false },
    { name: "price", location: "body", type: "string", required: false },
    { name: "ord_type", location: "body", type: "string", required: false, enum: ["limit", "price", "market", "best"] }
  ];
}

function identifierParameters(alternativeName: string, includeCurrency = false): ExchangeCatalogEndpoint["parameters"] {
  return [
    { name: "uuid", location: "query", type: "string", required: false },
    { name: alternativeName, location: "query", type: "string", required: false },
    ...(includeCurrency
      ? [{ name: "currency", location: "query", type: "string", required: false } as const]
      : [])
  ];
}
