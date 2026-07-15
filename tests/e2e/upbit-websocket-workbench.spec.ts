import { expect, test } from "@playwright/test";

test("고립된 업비트 웹소켓 작업대가 공개·비공개 제어와 raw 추적을 수행한다", async ({ page }) => {
  await page.addInitScript(() => {
    const sent: string[] = [];
    Object.defineProperty(window, "__upbitWebSocketSent", { value: sent, writable: false });
    class HarnessWebSocket extends EventTarget {
      static OPEN = 1;
      readyState = 0;
      constructor(public url: string) {
        super();
        setTimeout(() => { this.readyState = 1; this.dispatchEvent(new Event("open")); }, 0);
      }
      send(data: string) {
        sent.push(data);
        const control = JSON.parse(data) as Record<string, unknown>;
        const emit = (payload: unknown) => this.dispatchEvent(new MessageEvent("message", { data: JSON.stringify(payload) }));
        if (control.action === "connect") emit({ event: "connection", state: "connected", visibility: control.visibility, format: control.format });
        if (control.action === "subscribe") {
          emit({ event: "subscription", action: "subscribed" });
          if (control.endpoint_id === "websocket.ticker") emit({
            event: "frame", trace_id: "browser-trace", connection_id: "browser-connection", sequence: 1,
            received_at: "2026-07-16T00:00:00Z", payload: { type: "ticker", code: "KRW-BTC", trade_price: 150000000 },
            raw: "{\"type\":\"ticker\",\"trade_price\":150000000}", binary: true,
            provenance: { visibility: "public", format: "DEFAULT", endpoint_ids: ["websocket.ticker"] }
          });
        }
      }
      close() { this.readyState = 3; this.dispatchEvent(new Event("close")); }
    }
    Object.defineProperty(window, "WebSocket", { value: HarnessWebSocket, writable: true });
  });

  await page.goto("/src/features/upbitWebSocket/harness.html");
  const workbench = page.getByLabel("업비트 웹소켓 작업대");
  await expect(workbench).toBeVisible();
  await page.getByRole("button", { name: "연결", exact: true }).click();
  await expect(workbench.getByText("connected", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "구독", exact: true }).click();
  await expect(page.getByLabel("실시간 현재가")).toContainText("150,000,000");
  await page.getByRole("button", { name: "raw 추적" }).click();
  await expect(page.getByRole("dialog", { name: "raw frame 추적" })).toContainText("browser-trace");
  await page.getByRole("button", { name: "닫기" }).click();

  await page.getByRole("tab", { name: "내 자산" }).click();
  await page.getByRole("button", { name: "연결", exact: true }).click();
  await expect(workbench.getByText("connected", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "구독", exact: true }).click();
  const sent = await page.evaluate(() => (window as unknown as { __upbitWebSocketSent: string[] }).__upbitWebSocketSent);
  const controls = sent.map((item) => JSON.parse(item) as Record<string, unknown>);
  expect(controls).toEqual(expect.arrayContaining([
    expect.objectContaining({ action: "connect", visibility: "public" }),
    expect.objectContaining({ action: "connect", visibility: "private" }),
    expect.objectContaining({ action: "subscribe", endpoint_id: "websocket.my-asset", parameters: {} })
  ]));
  expect(sent.join(" ")).not.toMatch(/access_key|secret_key|authorization|bearer/i);

  await page.setViewportSize({ width: 390, height: 844 });
  expect(await workbench.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBeTruthy();
});

test("브라우저가 같은 출처 프록시를 거쳐 게이트웨이와 가짜 업비트에 연결한다", async ({ page }) => {
  await page.goto("/src/features/upbitWebSocket/harness.html");
  const workbench = page.getByLabel("업비트 웹소켓 작업대");

  await page.getByRole("button", { name: "연결", exact: true }).click();
  await expect(workbench.getByText("connected", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "구독", exact: true }).click();
  await expect(page.getByLabel("실시간 현재가")).toContainText("100");
  await page.getByRole("button", { name: "raw 추적" }).click();
  await expect(page.getByRole("dialog", { name: "raw frame 추적" })).toContainText(
    "public · DEFAULT · websocket.ticker"
  );
  await page.getByRole("button", { name: "닫기" }).click();

  await page.getByRole("tab", { name: "내 자산" }).click();
  await page.getByRole("button", { name: "연결", exact: true }).click();
  await expect(workbench.getByText("connected", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "구독", exact: true }).click();
  await expect(workbench.getByRole("status")).toContainText("subscribed");
});
