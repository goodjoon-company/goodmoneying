import type { TraceEnvelope } from "./types";

export type TraceFeedback = {
  error: string | null;
  cooldownMs: number;
};

export function describeTraceResponse(trace: TraceEnvelope, now = Date.now()): TraceFeedback {
  const status = trace.response.status_code;
  const limited = status === 418 || status === 429 || trace.rate_limit.remaining_sec === 0;
  const cooldownMs = limited ? retryDelayMilliseconds(trace.rate_limit.retry_after, now) : 0;
  const seconds = Math.max(1, Math.ceil(cooldownMs / 1_000));

  if (status >= 200 && status < 300) return { error: null, cooldownMs };
  if (status === 400) {
    return {
      error: "요청 조건이 올바르지 않습니다(HTTP 400). 입력값과 원본 추적을 확인하세요.",
      cooldownMs
    };
  }
  if (status === 418) {
    return {
      error: `업비트가 요청을 일시 차단했습니다(HTTP 418). ${seconds}초 후 다시 시도하고 원본 추적을 확인하세요.`,
      cooldownMs
    };
  }
  if (status === 429) {
    return {
      error: `요청 제한에 도달했습니다(HTTP 429). ${seconds}초 후 다시 시도하세요.`,
      cooldownMs
    };
  }
  return {
    error: `업비트 API 요청이 실패했습니다(HTTP ${status}). 원본 추적에서 오류 응답을 확인하세요.`,
    cooldownMs
  };
}

function retryDelayMilliseconds(retryAfter: string | null, now: number): number {
  if (retryAfter !== null) {
    const seconds = Number(retryAfter);
    if (Number.isFinite(seconds) && seconds >= 0) return Math.max(1_000, seconds * 1_000);
    const deadline = Date.parse(retryAfter);
    if (Number.isFinite(deadline)) return Math.max(1_000, deadline - now);
  }
  return 1_000;
}
