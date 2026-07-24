import { expect, type Page, test } from "@playwright/test";

const viewports = [
  { width: 1440, height: 1000 },
  { width: 1280, height: 900 },
  { width: 1024, height: 900 },
  { width: 900, height: 900 },
  { width: 760, height: 900 },
  { width: 390, height: 844 },
  { width: 360, height: 740 }
] as const;

test("P7 WCAG 2.2 AA proxy는 landmark·이름·키보드·대체 정보를 검증한다", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("main")).toBeVisible({ timeout: 60_000 });
  await expect(page.locator('[aria-label="제품 메뉴"]')).toBeVisible();
  await expect(page.getByRole("button", { name: "코인 분석" })).toBeVisible();
  await expect(page.getByRole("button", { name: "새로고침" })).toBeVisible();

  const unnamedButtons = await page.locator("button").evaluateAll((buttons) =>
    buttons
      .filter((button) => button.getClientRects().length > 0)
      .filter((button) => !(button.textContent?.trim() || button.getAttribute("aria-label")))
      .map((button) => button.outerHTML)
  );
  expect(unnamedButtons).toEqual([]);

  const imagesWithoutAlt = await page.locator("img").evaluateAll((images) =>
    images
      .filter((image) => image.getClientRects().length > 0)
      .filter((image) => !image.hasAttribute("alt"))
      .map((image) => image.outerHTML)
  );
  expect(imagesWithoutAlt).toEqual([]);

  await page.keyboard.press("Tab");
  const focusedTag = await page.evaluate(() => document.activeElement?.tagName.toLowerCase());
  expect(focusedTag).not.toBe("body");

  await page.getByRole("button", { name: "코인 분석" }).click();
  await expect(page.getByLabel("코인 분석 캔들 차트")).toBeVisible();
  await expect(page.getByLabel("집계 계보와 품질")).toContainText(/계산 .* · 품질 .* · 완전성/);
});

test("P7 7개 viewport는 본문 가로 overflow 없이 핵심 controls를 유지한다", async ({ page }) => {
  for (const viewport of viewports) {
    await page.setViewportSize(viewport);
    await page.goto("/");

    await expect(page.getByRole("button", { name: "코인 분석" })).toBeVisible({ timeout: 60_000 });
    await expect(page.getByRole("button", { name: "새로고침" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Quotation API 테스트" })).toBeVisible();
    await expect(page.locator(".app-shell")).toHaveCount(1);
    expect(await hasNoHorizontalOverflow(page)).toBeTruthy();

    await page.getByRole("button", { name: "코인 분석" }).click();
    await expect(page.getByLabel("코인 분석 화면")).toBeVisible();
    await expect(page.getByRole("button", { name: "4시간", exact: true })).toBeVisible();
    expect(await hasNoHorizontalOverflow(page)).toBeTruthy();
  }
});

async function hasNoHorizontalOverflow(page: Page) {
  return page.evaluate(() => {
    const documentFits = document.documentElement.scrollWidth <= window.innerWidth;
    const appShell = document.querySelector(".app-shell");
    const appFits = !appShell || appShell.scrollWidth <= appShell.clientWidth;
    return documentFits && appFits;
  });
}
