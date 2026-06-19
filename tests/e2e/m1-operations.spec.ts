import { expect, test } from "@playwright/test";

const apiBaseUrl = process.env.E2E_API_BASE_URL ?? "http://127.0.0.1:18000";
const operatorToken = process.env.E2E_OPERATOR_TOKEN ?? "local-dev-token";

test("M1 운영 화면에서 주요 시나리오를 탐색한다", async ({ page, request }) => {
  const universeResponse = await request.get(`${apiBaseUrl}/v1/candidate-universe`);
  expect(universeResponse.ok()).toBeTruthy();
  const universe = await universeResponse.json();
  const baselineEntries = universe.entries.slice(0, 50);
  const baselineTargetIds = baselineEntries.map(
    (entry: { instrument: { id: number } }) => entry.instrument.id
  );
  const firstInstrument = baselineEntries[0].instrument as {
    baseAsset: string;
    quoteCurrency: string;
  };
  const firstInstrumentName = `${firstInstrument.baseAsset} / ${firstInstrument.quoteCurrency}`;
  const resetResponse = await request.put(`${apiBaseUrl}/v1/collection-targets`, {
    headers: { "X-Operator-Token": operatorToken },
    data: {
      instrumentIds: baselineTargetIds,
      reason: "E2E baseline reset"
    }
  });
  expect(resetResponse.ok()).toBeTruthy();

  await page.goto("/");

  await expect(page.getByText("goodmoneying", { exact: true }).first()).toBeVisible({
    timeout: 60_000
  });
  await expect(page.getByRole("button", { name: "데이터 수집관리" })).toBeVisible();
  await expect(page.getByRole("button", { name: "종목 발굴" })).toBeDisabled();
  await expect(page.getByRole("heading", { name: "업비트 수집 운영 상태" })).toBeVisible();
  await expect(page.locator(".app-shell")).toHaveAttribute("data-theme", "dark");
  await expect(page.locator(".metric").filter({ hasText: "활성 수집 대상" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "코인별 수집 상태" })).toBeVisible();
  await expect(page.locator(".dashboard-row-button").first()).toBeVisible();
  await expect(page.getByText("최신수집중").first()).toBeVisible();
  await expect(page.getByText("KST").first()).toBeVisible();
  await expect(page.getByText("UTC")).toHaveCount(0);

  await page.locator(".dashboard-row-button").first().click();
  await expect(page.getByRole("heading", { name: "수집 계획" })).toBeVisible();
  await expect(page.getByText("2026-01-01 00:00 KST ~ 현재(지속)").first()).toBeVisible();
  await expect(page.getByRole("button", { name: "수정" })).toBeVisible();
  await expect(page.locator(".coverage-segment.missing").first()).toBeVisible();

  await page.getByRole("button", { name: "수집 대상 설정" }).click();
  await expect(page.getByRole("heading", { name: "후보 유니버스 상위 100개" })).toBeVisible();
  await expect(page.getByText("선택 50/50")).toBeVisible();
  await expect(page.getByText(firstInstrumentName)).toBeVisible();
  await page.locator(".target-row").nth(0).getByRole("checkbox").uncheck();
  await expect(page.getByText("선택 49/50")).toBeVisible();
  await page.locator(".target-row").nth(0).getByRole("checkbox").check();
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByText("선택 50/50")).toBeVisible();

  await page.getByRole("button", { name: "시장 리스트" }).click();
  await expect(page.getByRole("heading", { name: "시장 리스트" })).toBeVisible();
  await expect(page.getByText("등락률", { exact: true })).toBeVisible();
  await expect(page.getByText("24시간 거래대금", { exact: true })).toBeVisible();
  await expect(page.locator(".market-row-button")).toHaveCount(50);
  await page.locator(".market-row-button").first().click();

  await expect(page.getByRole("dialog", { name: "코인 상세" })).toBeVisible();
  await expect(page.locator(".detail-title")).toBeVisible();
  await expect(page.getByText("2026년 1월 1분봉")).toBeVisible();
  await expect(page.getByLabel("TradingView 캔들 차트")).toBeVisible();
  await expect(page.getByText("현재가 게이지")).toBeVisible();
  await expect(page.locator(".modal-backdrop")).toBeVisible();
});
