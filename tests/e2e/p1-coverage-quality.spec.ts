import { expect, test } from "@playwright/test";

test("P1 커버리지 정책을 데스크톱과 모바일에서 운영한다", async ({ page }, testInfo) => {
  const runtimeErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") runtimeErrors.push(message.text());
  });
  page.on("pageerror", (error) => runtimeErrors.push(error.message));

  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await page.getByRole("button", { name: "Coverage & Quality" }).click();

  await expect(page.getByRole("heading", { name: "Coverage & Quality" })).toBeVisible();
  await expect(page.getByText("2024-01-01 00:00 UTC")).toBeVisible();
  for (const [state, label] of [
    ["available", "사용 가능"],
    ["no_trade", "무거래 확인"],
    ["missing", "복구 필요"],
    ["unavailable", "획득 불가"],
    ["unverified", "미검증"]
  ]) {
    await expect(page.getByText(`${state} · ${label}`)).toBeVisible();
  }
  await expect(
    page.getByText("동일 성공 페이지의 양쪽 인접 캔들로 내부 무체결을 확인")
  ).toBeVisible();
  await expect(page.getByRole("row", { name: /KRW-BTC 비트코인/ })).toBeVisible();

  await page.getByRole("button", { name: "KRW-BTC 일시정지" }).click();
  await expect(page.getByRole("status")).toContainText("KRW-BTC 일시정지 요청을 저장했습니다.");
  const resumeButton = page.getByRole("button", { name: "KRW-BTC 재개" });
  await expect(resumeButton).toBeVisible();
  await resumeButton.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("status")).toContainText("KRW-BTC 재개 요청을 저장했습니다.");
  await expect(page.getByRole("button", { name: "KRW-BTC 일시정지" })).toBeVisible();

  await page.getByRole("button", { name: "KRW-BTC 정책 편집" }).click();
  await expect(page.getByRole("dialog", { name: "KRW-BTC 정책 편집" })).toBeVisible();
  await page.getByLabel("티커 스냅숏").uncheck();
  await page.getByLabel("보존 기간 일수").fill("3650");
  await page.getByLabel("우선순위").fill("250");
  await page.getByRole("button", { name: "KRW-BTC 정책 저장" }).click();
  await expect(page.getByRole("status")).toContainText("KRW-BTC 정책 저장 요청을 저장했습니다.");
  await expect(page.getByRole("dialog", { name: "KRW-BTC 정책 편집" })).toHaveCount(0);

  await page.screenshot({
    path: testInfo.outputPath("p1-coverage-quality-desktop.png"),
    fullPage: true
  });

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("heading", { name: "Coverage & Quality" })).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({
    path: testInfo.outputPath("p1-coverage-quality-mobile.png"),
    fullPage: true
  });
  expect(runtimeErrors).toEqual([]);
});
