import { describe, expect, it } from "vitest";

import { describeTraceResponse } from "./trace";
import type { TraceEnvelope } from "./types";

const trace = (statusCode: number, retryAfter: string | null = null): TraceEnvelope => ({
  trace_id: "trace-id",
  endpoint_id: "rest.test",
  request: { method: "GET", path: "/v1/test", parameters: {} },
  response: { status_code: statusCode, body: {} },
  rate_limit: { group: "market", remaining_sec: statusCode === 429 ? 0 : 9, retry_after: retryAfter },
  duration_ms: 1,
  received_at: "2026-07-16T00:00:00Z"
});

describe("추적 응답 사용자 피드백", () => {
  it.each([
    [400, "요청 조건"],
    [418, "일시 차단"],
    [429, "요청 제한"],
    [500, "요청이 실패"]
  ])("HTTP %i를 상태별 친화 오류로 설명한다", (statusCode, expectedMessage) => {
    expect(describeTraceResponse(trace(statusCode), Date.parse("2026-07-16T00:00:00Z")).error)
      .toContain(expectedMessage);
  });

  it("숫자와 HTTP 날짜 Retry-After를 냉각 시간으로 변환한다", () => {
    const now = Date.parse("2026-07-16T00:00:00Z");
    expect(describeTraceResponse(trace(429, "2"), now).cooldownMs).toBe(2_000);
    expect(describeTraceResponse(trace(418, "Thu, 16 Jul 2026 00:00:05 GMT"), now).cooldownMs)
      .toBe(5_000);
  });
});
