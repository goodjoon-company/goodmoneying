import { expect, test } from "@playwright/test";

test("Backtest Lab은 저장된 run 결과를 읽기 전용으로 탐색한다", async ({ page }) => {
  const runtimeIssues: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") runtimeIssues.push(message.text());
  });
  page.on("pageerror", (error) => runtimeIssues.push(error.message));

  await page.goto("/");
  await page.getByRole("button", { name: "Backtest Lab" }).click();

  const lab = page.locator(".backtest-lab");
  await expect(lab.getByRole("heading", { name: "Backtest Lab" })).toBeVisible();
  await expect(page.getByLabel("화면 갱신 기준").getByText("REST 조회", { exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "저장된 백테스트 run 목록" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Run #21 선택" })).toBeVisible();
  await expect(lab.locator(".backtest-lab-grid h3").filter({ hasText: "Run #21" })).toBeVisible();
  await expect(page.getByText("finalEquity", { exact: true })).toBeVisible();
  await expect(page.getByText("1009.579790", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("table", { name: "백테스트 체결 결과" })).toContainText("partially_filled");
  await expect(page.getByText("walk_forward_summary")).toBeVisible();
  await expect(page.getByRole("button", { name: "백테스트 실행" })).toHaveCount(0);

  await page.getByLabel("백테스트 Run ID").fill("21");
  await page.getByRole("button", { name: "Run 조회" }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(lab.getByRole("heading", { name: "Backtest Lab" })).toBeVisible();
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth))
    .toBeLessThanOrEqual(1);
  expect(runtimeIssues).toEqual([]);
});
