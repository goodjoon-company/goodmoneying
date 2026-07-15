import { afterEach, describe, expect, it, vi } from "vitest";

import { createUpbitGatewayClient } from "./client";

afterEach(() => vi.unstubAllGlobals());

describe("업비트 게이트웨이 클라이언트", () => {
  it("임의 업비트 URL이나 인증 없이 endpoint_id만 게이트웨이에 전송한다", async () => {
    const fetchMock = vi.fn(async () => Response.json({ trace_id: "trace-1" }));
    vi.stubGlobal("fetch", fetchMock);
    const client = createUpbitGatewayClient("/upbit-gateway");

    await client.execute("rest.list-tickers", { markets: "KRW-BTC" });

    expect(fetchMock).toHaveBeenCalledWith("/upbit-gateway/v1/requests", expect.objectContaining({
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ endpoint_id: "rest.list-tickers", parameters: { markets: "KRW-BTC" } })
    }));
    expect(JSON.stringify(fetchMock.mock.calls)).not.toContain("api.upbit.com");
    expect(JSON.stringify(fetchMock.mock.calls)).not.toContain("Authorization");
  });
});
