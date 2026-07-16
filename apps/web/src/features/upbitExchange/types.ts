import type { CatalogParameter as SharedCatalogParameter } from "../../components/upbit-api-test/types";

export type ExchangeSafety = "read" | "test" | "blocked";
export type ExchangeFunctionalGroup =
  | "pocket"
  | "asset"
  | "order"
  | "withdrawal"
  | "deposit"
  | "travel_rule"
  | "service";

export type CatalogParameter = SharedCatalogParameter;

export type ExchangeCatalogEndpoint = {
  endpoint_id: string;
  title: string;
  category: "exchange";
  functional_group: ExchangeFunctionalGroup;
  method: string;
  path: string;
  parameters: CatalogParameter[];
  any_of_required?: string[][];
  mutually_exclusive?: string[][];
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
