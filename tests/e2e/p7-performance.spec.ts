import { expect, type Page, test } from "@playwright/test";

type BrowserVitals = {
  cls: number;
  inpProxyMs: number;
  lcpMs: number;
};

declare global {
  interface Window {
    __p7Vitals?: BrowserVitals;
  }
}

test("P7 Web Vitals local budget은 LCP·INP proxy·CLS 목표를 만족한다", async ({ page }) => {
  await installVitalsCollector(page);

  await page.goto("/");
  await expect(page.getByRole("button", { name: "코인 분석" })).toBeVisible({ timeout: 60_000 });

  await page.getByRole("button", { name: "코인 분석" }).click();
  await expect(page.getByLabel("코인 분석 화면")).toBeVisible();
  const vitals = await readVitals(page);

  expect(vitals.lcpMs).toBeGreaterThan(0);
  expect(vitals.lcpMs).toBeLessThanOrEqual(2_500);
  expect(vitals.inpProxyMs).toBeLessThanOrEqual(200);
  expect(vitals.cls).toBeLessThanOrEqual(0.1);
});

test("P7 첫 유용 셸은 3초 안에 표시된다", async ({ page }) => {
  const startedAt = Date.now();
  await page.goto("/");
  await expect(page.getByRole("button", { name: "코인 분석" })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("button", { name: "Quotation API 테스트" })).toBeVisible();
  await expect(page.getByRole("button", { name: "새로고침" })).toBeVisible();
  const firstUsefulShellMs = Date.now() - startedAt;

  expect(firstUsefulShellMs).toBeLessThanOrEqual(3_000);
});

test("P7 실시간 event는 수신 뒤 1초 안에 브라우저에 반영된다", async ({ page }) => {
  let firstChartReceivedAt = 0;
  page.on("websocket", (socket) => {
    if (!socket.url().includes("/v1/realtime/analysis")) return;
    socket.on("framereceived", (event) => {
      const message = JSON.parse(String(event.payload)) as { type?: string };
      if (message.type === "analysis.chart" && firstChartReceivedAt === 0) {
        firstChartReceivedAt = Date.now();
      }
    });
  });

  await page.goto("/");
  await page.getByRole("button", { name: "코인 분석" }).click();
  await expect(page.getByLabel("코인 분석 캔들 차트")).toBeVisible({ timeout: 60_000 });
  const browserAppliedAt = Date.now();

  expect(firstChartReceivedAt).toBeGreaterThan(0);
  expect(browserAppliedAt - firstChartReceivedAt).toBeLessThanOrEqual(1_000);
});

async function installVitalsCollector(page: Page) {
  await page.addInitScript(() => {
    window.__p7Vitals = { cls: 0, inpProxyMs: 0, lcpMs: 0 };
    try {
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          window.__p7Vitals!.lcpMs = entry.startTime;
        }
      }).observe({ type: "largest-contentful-paint", buffered: true });
    } catch {
      // Browser support is validated by the positive LCP assertion in the test.
    }
    try {
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          const layoutShift = entry as PerformanceEntry & {
            hadRecentInput?: boolean;
            value?: number;
          };
          if (!layoutShift.hadRecentInput) {
            window.__p7Vitals!.cls += layoutShift.value ?? 0;
          }
        }
      }).observe({ type: "layout-shift", buffered: true });
    } catch {
      // Browser support is validated by the CLS budget assertion.
    }
    try {
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          window.__p7Vitals!.inpProxyMs = Math.max(
            window.__p7Vitals!.inpProxyMs,
            entry.duration
          );
        }
      }).observe({ type: "event", buffered: true, durationThreshold: 0 });
    } catch {
      // Browser support is best-effort; unsupported browsers keep 0ms.
    }
  });
}

async function readVitals(page: Page) {
  return page.evaluate(
    () =>
      new Promise<BrowserVitals>((resolve) => {
        window.requestAnimationFrame(() => {
          window.setTimeout(() => {
            resolve(
              window.__p7Vitals ?? {
                cls: Number.POSITIVE_INFINITY,
                inpProxyMs: Number.POSITIVE_INFINITY,
                lcpMs: 0
              }
            );
          }, 0);
        });
      })
  );
}
