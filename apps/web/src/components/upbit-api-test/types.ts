import type { ComponentType } from "react";

export type WorkbenchModuleId = "quotation" | "exchange" | "websocket";
export type ParameterValue = string | number | boolean | string[];
export type RequestParameters = Record<string, ParameterValue>;

export type CatalogParameter = {
  name: string;
  location: "path" | "query" | "body";
  type: "string" | "integer" | "number" | "boolean" | "array";
  required: boolean;
  format?: string;
  enum?: Array<string | number>;
  minimum?: number;
  maximum?: number;
};

export type CatalogEndpoint = {
  endpoint_id: string;
  title: string;
  category: "quotation" | "exchange";
  functional_group: string;
  deprecated?: boolean;
  method: string;
  path: string;
  parameters: CatalogParameter[];
  rate_limit_group: string;
  safety: "read" | "test" | "blocked";
  source_url: string;
};

export type UpbitCatalog = {
  catalog_version: string;
  verified_at: string;
  official_baseline: string;
  rest_endpoints: CatalogEndpoint[];
};

export type TraceEnvelope = {
  trace_id: string;
  endpoint_id: string;
  request: { method: string; path: string; parameters: RequestParameters };
  response: { status_code: number; body: unknown };
  rate_limit: { group: string; remaining_sec: number | null; retry_after: string | null };
  duration_ms: number;
  received_at: string;
};

export type WorkbenchContext = {
  market: string;
  quote: string;
  base: string;
};

export type WorkbenchModuleExtension = {
  id: WorkbenchModuleId;
  label: string;
  Component: ComponentType<WorkbenchExtensionProps>;
};

export type WorkbenchExtensionProps = {
  context: WorkbenchContext;
  onContextChange: (context: WorkbenchContext) => void;
};

export type CandleRow = {
  startedAt: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  tradeAmount: number;
  raw: Record<string, unknown>;
};
