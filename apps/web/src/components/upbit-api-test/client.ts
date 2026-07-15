import type { RequestParameters, TraceEnvelope, UpbitCatalog } from "./types";

export type UpbitGatewayClient = {
  loadCatalog(signal?: AbortSignal): Promise<UpbitCatalog>;
  execute(endpointId: string, parameters: RequestParameters, signal?: AbortSignal): Promise<TraceEnvelope>;
};

export function createUpbitGatewayClient(
  baseUrl = import.meta.env.VITE_UPBIT_GATEWAY_BASE_URL ?? "/upbit-gateway"
): UpbitGatewayClient {
  const request = async <T>(path: string, init?: RequestInit): Promise<T> => {
    const response = await fetch(`${baseUrl}${path}`, init);
    const payload: unknown = await response.json();
    if (!response.ok && !isTraceEnvelope(payload)) {
      const message = isErrorResponse(payload) ? payload.detail.message : `HTTP ${response.status}`;
      throw new Error(`업비트 게이트웨이 요청에 실패했습니다: ${message}`);
    }
    return payload as T;
  };
  return {
    loadCatalog: (signal) => request<UpbitCatalog>("/v1/catalog", { signal }),
    execute: (endpointId, parameters, signal) => request<TraceEnvelope>("/v1/requests", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ endpoint_id: endpointId, parameters }),
      signal
    })
  };
}

function isTraceEnvelope(value: unknown): value is TraceEnvelope {
  return isRecord(value) && typeof value.trace_id === "string";
}

function isErrorResponse(value: unknown): value is { detail: { message: string } } {
  return isRecord(value) && isRecord(value.detail) && typeof value.detail.message === "string";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
