import { expect, test } from "@playwright/test";

test("Data Lab은 build 생성, 불변 version, coverage, exact member를 REST polling으로 탐색한다", async ({ page }) => {
  const runtimeIssues: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") runtimeIssues.push(message.text());
  });
  page.on("pageerror", (error) => runtimeIssues.push(error.message));

  await page.goto("/");
  await page.getByRole("button", { name: "Data Lab" }).click();

  const dataLab = page.locator(".data-lab");
  await expect(dataLab.getByRole("heading", { name: "Data Lab" })).toBeVisible();
  await expect(page.getByLabel("화면 갱신 기준").getByText("REST polling", { exact: true })).toBeVisible();

  await expect(page.getByLabel("데이터셋 build 생성")).toBeVisible();
  await expect(page.getByLabel("시장")).toHaveValue("41");
  await expect(page.getByText("Build #7")).toBeVisible();
  await expect(page.getByText("retry_wait")).toBeVisible();

  await expect(page.getByText("Version #12")).toBeVisible();
  await expect(page.getByText("Version #11")).toBeVisible();
  await expect(page.getByText("available 40")).toBeVisible();
  await expect(page.getByText("unverified 40")).toBeVisible();
  await expect(page.getByRole("img", { name: "series exact member chart" })).toBeVisible();
  await expect(page.getByRole("table", { name: "series exact member table" })).toBeVisible();
  await expect(page.getByText("open 100 · close 101")).toBeVisible();
  await expect(page.getByText("A/B 41 · candle · 1m")).toBeVisible();

  await page.getByLabel("사유").fill("P2-6 E2E 신규 build");
  await page.getByRole("button", { name: "신규 build 생성" }).click();
  await expect(page.getByText("Build #8")).toBeVisible();
  await expect(page.getByText("pending")).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(dataLab.getByRole("heading", { name: "Data Lab" })).toBeVisible();
  await expect(page.getByRole("table", { name: "series exact member table" })).toBeVisible();
  const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(scrollWidth).toBeLessThanOrEqual(390);

  expect(runtimeIssues).toEqual([]);
});

test("Data Lab은 운영 작업대 밀도를 유지하며 주요 폭에서 가로 overflow 없이 표시된다", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Data Lab" }).click();
  await expect(page.locator(".data-lab").getByRole("heading", { name: "Data Lab" })).toBeVisible();

  for (const width of [1440, 1280, 1024, 900, 760, 390, 360]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(page.getByLabel("데이터셋 build 생성")).toBeVisible();
    await expect(page.getByText("Version #12")).toBeVisible();
    await page.screenshot({
      path: `test-results/p2-data-lab-${width}.png`,
      fullPage: true
    });
    await expect
      .poll(() =>
        page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
      )
      .toBeLessThanOrEqual(1);
  }
});
