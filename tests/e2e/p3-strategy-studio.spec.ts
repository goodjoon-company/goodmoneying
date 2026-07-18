import { expect, test } from "@playwright/test";

test("Strategy Studio는 마우스 없이 graph 검증 오류를 고치고 불변 version을 게시한다", async ({ page }) => {
  const runtimeIssues: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") runtimeIssues.push(message.text());
  });
  page.on("pageerror", (error) => runtimeIssues.push(error.message));

  await page.goto("/");
  await page.getByRole("button", { name: "Strategy Studio" }).focus();
  await page.keyboard.press("Enter");

  await expect(page.getByRole("heading", { name: "Strategy Studio" })).toBeVisible();
  await expect(page.getByRole("img", { name: "전략 그래프 포인터 뷰" })).toContainText("market.close → signal.price");
  await expect(page.getByRole("table", { name: "전략 그래프 텍스트 대안" })).toBeVisible();
  await expect(page.getByRole("list", { name: "전략 그래프 edge 목록" })).toContainText("market.close → signal.price");
  await expect(page.getByRole("group", { name: "키보드 대체 편집기" })).toBeVisible();

  await page.getByLabel("출력 신호 이름").focus();
  await page.keyboard.press(process.platform === "darwin" ? "Meta+A" : "Control+A");
  await page.keyboard.type("exit_long");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "출력 신호 적용" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByText("출력 exit_long")).toBeVisible();

  await page.getByRole("button", { name: "순환 오류 edge 추가" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("img", { name: "전략 그래프 포인터 뷰" })).toContainText("signal.exit_long → market.close");
  await expect(page.getByRole("list", { name: "전략 그래프 edge 목록" })).toContainText("signal.exit_long → market.close");
  await page.getByRole("button", { name: "서버 검증" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("alert", { name: "전략 그래프 검증 오류" })).toContainText("cycle_detected");
  await expect(page.getByRole("alert", { name: "전략 그래프 검증 오류" })).toContainText("node -");

  await page.getByRole("button", { name: "순환 오류 edge 제거" }).focus();
  await page.keyboard.press("Enter");
  await page.getByRole("button", { name: "서버 검증" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("status", { name: "전략 그래프 검증 상태" })).toContainText("검증 통과");
  await page.getByRole("button", { name: "불변 version 게시" }).focus();
  await page.keyboard.press("Enter");

  await expect(page.getByText("Version #1")).toBeVisible();
  await expect(page.getByText("published · 불변 version")).toBeVisible();
  await expect(page.getByText(/^[0-9a-f]{64}$/)).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("heading", { name: "Strategy Studio" })).toBeVisible();
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth))
    .toBeLessThanOrEqual(1);
  expect(runtimeIssues).toEqual([]);
});
