import { expect, test } from "@playwright/test";

test("Bot Workshop은 P5 paper/shadow 운영 경계와 live 잠금을 표시한다", async ({ page }) => {
  const runtimeIssues: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") runtimeIssues.push(message.text());
  });
  page.on("pageerror", (error) => runtimeIssues.push(error.message));

  await page.goto("/");
  await page.getByRole("button", { name: "Bot Workshop" }).click();

  const workshop = page.locator(".bot-workshop");
  await expect(workshop.getByRole("heading", { name: "Bot Workshop" })).toBeVisible();
  await expect(page.getByText("P5-6 · Bot Workshop")).toBeVisible();
  await expect(page.getByLabel("화면 갱신 기준").getByText("REST 준비", { exact: true })).toBeVisible();
  await expect(page.getByText("Portfolio allocation → paper 운영 준비")).toBeVisible();
  await expect(workshop.getByText("draft · 설계 중", { exact: true })).toBeVisible();
  await expect(workshop.getByText("backtest_ready · 백테스트 준비", { exact: true })).toBeVisible();
  await expect(workshop.getByText("paper · paper rehearsal", { exact: true })).toBeVisible();
  await expect(workshop.getByText("shadow · shadow rehearsal", { exact: true })).toBeVisible();
  await expect(workshop.getByText("live_ready · 안전 잠금", { exact: true })).toBeVisible();
  await expect(workshop.getByText("live · 안전 잠금", { exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "주문 파이프라인" })).toContainText("paper execution job");
  await expect(page.getByRole("region", { name: "주문 파이프라인" })).toContainText("position projection");
  await expect(page.getByRole("status", { name: "live 안전 잠금" })).toContainText("live-ready · live 잠금");
  await expect(page.getByRole("region", { name: "킬스위치와 승인 checklist" })).toContainText("global kill switch");
  await expect(page.getByRole("region", { name: "대사 증적" })).toContainText("reconciliation_mismatch");
  await expect(page.getByRole("region", { name: "대사 증적" })).toContainText("outcome_unknown");
  await expect(page.getByRole("button", { name: /주문.*제출|live.*활성화/i })).toHaveCount(0);
  await expect(page.getByText("private WebSocket")).toHaveCount(0);
  await expect(page.getByText("주문 테스트 API")).toHaveCount(0);
  await expect(page.getByText(/주식|stock/i)).toHaveCount(0);

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(workshop.getByRole("heading", { name: "Bot Workshop" })).toBeVisible();
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth))
    .toBeLessThanOrEqual(1);
  expect(runtimeIssues).toEqual([]);
});
