export type ExchangeSafety = "read" | "test" | "blocked";
export type ExchangeFunctionalGroup =
  | "pocket"
  | "asset"
  | "order"
  | "withdrawal"
  | "deposit"
  | "travel_rule"
  | "service";

export type CatalogParameter = {
  name: string;
  location: "path" | "query" | "body";
  type: "string" | "integer" | "number" | "boolean" | "array";
  items?: "string" | "integer" | "number" | "boolean";
  required: boolean;
  format?: string;
  enum?: Array<string | number>;
  minimum?: number;
  maximum?: number;
};

export type ExchangeCatalogEndpoint = {
  endpoint_id: string;
  title: string;
  category: "exchange";
  functional_group: ExchangeFunctionalGroup;
  method: string;
  path: string;
  parameters: CatalogParameter[];
  any_of_required?: string[][];
  rate_limit_group: string;
  safety: ExchangeSafety;
  source_url: string;
};

export type ExchangeCatalog = {
  catalog_version: string;
  verified_at: string;
  rest_endpoints: ExchangeCatalogEndpoint[];
};

export type GatewayHealth = {
  status: "ok";
  service: "upbit-gateway";
  catalog_version: string;
  credentials_configured: boolean;
};

export type GatewayRequest = {
  endpoint_id: string;
  parameters: Record<string, unknown>;
};

export type TraceEnvelope = {
  trace_id: string;
  endpoint_id: string;
  request: { method: string; path: string; parameters: Record<string, unknown> };
  response: { status_code: number; body: unknown };
  rate_limit: { group: string; remaining_sec: number | null; retry_after: string | null };
  duration_ms: number;
  received_at: string;
};

export interface ExchangeGateway {
  getHealth(): Promise<GatewayHealth>;
  getCatalog(): Promise<ExchangeCatalog>;
  execute(request: GatewayRequest): Promise<TraceEnvelope>;
}

export interface ExchangeMarketConceptAdapter {
  normalize(value: string): string;
  suggestions: string[];
  inputLabel: string;
}
