import { expect, test } from "@playwright/test";

const apiBaseUrl = process.env.E2E_API_BASE_URL ?? "http://127.0.0.1:18000";
const operatorToken = "local-dev-token";

test("M1 운영 화면에서 주요 시나리오를 탐색한다", async ({ page, request }) => {
  const universeResponse = await request.get(`${apiBaseUrl}/v1/candidate-universe`);
  expect(universeResponse.ok()).toBeTruthy();
  const universe = await universeResponse.json();
  const baselineTargetIds = universe.entries
    .slice(0, 50)
    .map((entry: { instrument: { id: number } }) => entry.instrument.id);
  const resetResponse = await request.put(`${apiBaseUrl}/v1/collection-targets`, {
    headers: { "X-Operator-Token": operatorToken },
    data: {
      instrumentIds: baselineTargetIds,
      reason: "E2E baseline reset"
    }
  });
  expect(resetResponse.ok()).toBeTruthy();

  await page.goto("/");

  await expect(page.getByText("goodmoneying")).toBeVisible();
  await expect(page.getByRole("button", { name: "데이터 수집관리" })).toBeVisible();
  await expect(page.getByRole("button", { name: "종목 발굴" })).toBeDisabled();
  await expect(page.getByRole("heading", { name: "운영 상태 대시보드" })).toBeVisible();
  await expect(page.locator(".app-shell")).toHaveAttribute("data-theme", "dark");
  await expect(page.locator(".metric").filter({ hasText: "활성 수집 대상" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "코인별 수집 상태" })).toBeVisible();
  await expect(page.getByRole("button", { name: /KRW-BTC/ })).toBeVisible();
  await expect(page.getByText("최신수집중").first()).toBeVisible();
  await expect(page.getByText("KST").first()).toBeVisible();
  await expect(page.getByText("UTC").first()).toBeVisible();

  await page.getByRole("button", { name: /KRW-BTC/ }).click();
  await expect(page.getByRole("heading", { name: "수집 계획" })).toBeVisible();
  await expect(page.getByText("2026-01-01 00:00 KST ~ 현재(지속)").first()).toBeVisible();
  await expect(page.getByRole("button", { name: "수정" })).toBeVisible();
  await expect(page.locator(".coverage-segment.missing").first()).toBeVisible();

  await page.getByRole("button", { name: "수집 대상 설정" }).click();
  await expect(page.getByRole("heading", { name: "후보 유니버스 상위 100개" })).toBeVisible();
  await expect(page.getByText("선택 50/최대 50")).toBeVisible();
  await expect(page.getByText("KRW-BTC")).toBeVisible();
  await page.locator(".target-item").nth(0).getByRole("checkbox").uncheck();
  await expect(page.getByText("선택 49/최대 50")).toBeVisible();
  await page.locator(".target-item").nth(0).getByRole("checkbox").check();
  await page.getByRole("button", { name: "저장" }).click();
  await expect(page.getByText("선택 50/최대 50")).toBeVisible();

  await page.getByRole("button", { name: "시장 리스트" }).click();
  await expect(page.getByRole("heading", { name: "시장 리스트" })).toBeVisible();
  await expect(page.getByText("등락률")).toBeVisible();
  await expect(page.getByText("24시간 거래대금")).toBeVisible();
  await expect(page.getByText("KRW-BTC")).toBeVisible();
  await expect(page.locator(".market-row-button")).toHaveCount(50);
  await page.getByRole("button", { name: /KRW-BTC/ }).click();

  await expect(page.getByRole("dialog", { name: "코인 상세" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "KRW-BTC" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "캔들 흐름" })).toBeVisible();
  await expect(page.getByText("2026년 1월 1분봉")).toBeVisible();
  await expect(page.getByText("UTC 기준 2026-01-01 00:00 ~ 2026-02-01 00:00")).toBeVisible();
  await expect(page.getByLabel("캔들 차트")).toBeVisible();
  await expect(page.locator(".modal-backdrop")).toBeVisible();
});
