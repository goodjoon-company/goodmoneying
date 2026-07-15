import type {
  ExchangeCatalog,
  ExchangeGateway,
  GatewayHealth,
  GatewayRequest,
  TraceEnvelope
} from "./types";

type Fetch = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

export class GatewayHttpError extends Error {
  constructor(readonly status: number) {
    super(friendlyGatewayError(status));
    this.name = "GatewayHttpError";
  }
}

export function createHttpExchangeGateway(
  baseUrl: string,
  fetcher: Fetch = (input, init) => fetch(input, init)
): ExchangeGateway {
  const normalizedBase = baseUrl.replace(/\/$/, "");
  const request = async <T>(
    path: string,
    init?: RequestInit,
    preserveTraceEnvelope = false
  ): Promise<T> => {
    const response = await fetcher(`${normalizedBase}${path}`, {
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      ...init
    });
    const body: unknown = await response.json();
    if (!response.ok && !(preserveTraceEnvelope && isTraceEnvelope(body))) {
      throw new GatewayHttpError(response.status);
    }
    return body as T;
  };
  return {
    getHealth: () => request<GatewayHealth>("/health"),
    getCatalog: () => request<ExchangeCatalog>("/v1/catalog"),
    execute: (payload: GatewayRequest) => request<TraceEnvelope>(
      "/v1/requests",
      {
        method: "POST",
        body: JSON.stringify(payload)
      },
      true
    )
  };
}

function isTraceEnvelope(value: unknown): value is TraceEnvelope {
  if (!isRecord(value)) return false;
  const trace = value as Partial<TraceEnvelope>;
  return typeof trace.trace_id === "string"
    && typeof trace.endpoint_id === "string"
    && isRecord(trace.request)
    && typeof trace.request.method === "string"
    && typeof trace.request.path === "string"
    && isRecord(trace.request.parameters)
    && isRecord(trace.response)
    && typeof trace.response.status_code === "number"
    && "body" in trace.response
    && isRecord(trace.rate_limit)
    && typeof trace.rate_limit.group === "string"
    && typeof trace.duration_ms === "number"
    && typeof trace.received_at === "string";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function friendlyGatewayError(status: number, _unsafeDetail?: string): string {
  if (status === 400) return "요청 값을 확인해 주세요.";
  if (status === 401) return "API Key 권한과 허용 IP를 확인해 주세요.";
  if (status === 418) return "업비트 요청이 일시 차단됐습니다. 제한 해제 뒤 다시 시도해 주세요.";
  if (status === 422) return "입력 형식을 확인해 주세요.";
  if (status === 429) return "요청 수 제한에 도달했습니다. 잠시 뒤 다시 시도해 주세요.";
  if (status === 503) return "서버에 API Key가 설정되지 않았습니다.";
  if (status >= 500) return "게이트웨이 또는 업비트 서버 응답을 확인해 주세요.";
  return `요청을 처리하지 못했습니다 (HTTP ${status}).`;
}
