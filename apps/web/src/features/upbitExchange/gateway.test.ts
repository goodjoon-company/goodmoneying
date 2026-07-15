import { describe, expect, it, vi } from "vitest";
import { createHttpExchangeGateway, friendlyGatewayError } from "./gateway";
import { traceFor } from "./testFixtures";

describe("Exchange 게이트웨이 클라이언트", () => {
  it("키를 받지 않고 상태·카탈로그·endpoint_id 요청만 전송한다", async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(Response.json({
        status: "ok",
        service: "upbit-gateway",
        catalog_version: "1.6.3",
        credentials_configured: true
      }))
      .mockResolvedValueOnce(Response.json({ catalog_version: "1.6.3", verified_at: "2026-07-16", rest_endpoints: [] }))
      .mockResolvedValueOnce(Response.json({ trace_id: "trace" }));
    const gateway = createHttpExchangeGateway("/exchange-gateway", fetch);

    await gateway.getHealth();
    await gateway.getCatalog();
    await gateway.execute({ endpoint_id: "rest.get-balance", parameters: {} });

    expect(fetch).toHaveBeenNthCalledWith(1, "/exchange-gateway/health", expect.objectContaining({ credentials: "same-origin" }));
    expect(fetch).toHaveBeenNthCalledWith(2, "/exchange-gateway/v1/catalog", expect.anything());
    expect(fetch).toHaveBeenNthCalledWith(
      3,
      "/exchange-gateway/v1/requests",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ endpoint_id: "rest.get-balance", parameters: {} })
      })
    );
    expect(JSON.stringify(fetch.mock.calls)).not.toMatch(/access.?key|secret|authorization|jwt/i);
  });

  it.each([
    [400, "요청 값을 확인해 주세요"],
    [401, "API Key 권한과 허용 IP를 확인해 주세요"],
    [418, "일시 차단"],
    [422, "입력 형식"],
    [429, "요청 수 제한"],
    [500, "게이트웨이 또는 업비트 서버"],
    [503, "서버에 API Key가 설정되지 않았습니다"]
  ])("HTTP %i를 안전한 사용자 메시지로 바꾼다", (status, expected) => {
    expect(friendlyGatewayError(status, "RAW_SECRET_SHOULD_NOT_APPEAR")).toContain(expected);
    expect(friendlyGatewayError(status, "RAW_SECRET_SHOULD_NOT_APPEAR")).not.toContain("RAW_SECRET");
  });

  it.each([400, 401, 418, 422, 429, 500])(
    "HTTP %i 응답의 마스킹된 추적 봉투를 폐기하지 않는다",
    async (status) => {
      const trace = traceFor("rest.get-balance", { error: { name: "safe_error" } }, status);
      const fetch = vi.fn().mockResolvedValue(Response.json(trace, { status }));
      const gateway = createHttpExchangeGateway("/exchange-gateway", fetch);

      await expect(gateway.execute({ endpoint_id: "rest.get-balance", parameters: {} }))
        .resolves.toEqual(trace);
    }
  );

  it("불완전한 오류 객체를 추적 봉투로 오인하지 않는다", async () => {
    const fetch = vi.fn().mockResolvedValue(Response.json({
      trace_id: "partial",
      endpoint_id: "rest.get-balance",
      response: { status_code: 500 }
    }, { status: 500 }));
    const gateway = createHttpExchangeGateway("/exchange-gateway", fetch);

    await expect(gateway.execute({ endpoint_id: "rest.get-balance", parameters: {} }))
      .rejects.toMatchObject({ status: 500 });
  });
});
