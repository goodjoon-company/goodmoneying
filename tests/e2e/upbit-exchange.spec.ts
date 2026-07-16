import { expect, test, type Page } from "@playwright/test";

const route = "/src/features/upbitExchange/e2e.html";

test("Exchange 38개 기능을 그룹별로 탐색하고 조회 결과와 원본 추적을 확인한다", async ({ page }) => {
  const upstreamRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("api.upbit.com")) upstreamRequests.push(request.url());
  });
  await page.goto(route);

  await expect(page.getByRole("main", { name: "Exchange API 작업대" })).toBeVisible();
  await expect(page.getByRole("tab", { name: /포켓/ })).toContainText("7");
  await expect(page.getByRole("tab", { name: /계정/ })).toContainText("1");
  await expect(page.getByRole("tab", { name: /주문/ })).toContainText("11");
  await expect(page.getByRole("tab", { name: /출금/ })).toContainText("7");
  await expect(page.getByRole("tab", { name: /입금/ })).toContainText("7");
  await expect(page.getByRole("tab", { name: /Travel Rule/ })).toContainText("3");
  await expect(page.getByRole("tab", { name: /서비스/ })).toContainText("2");

  await page.getByRole("tab", { name: /계정/ }).click();
  await page.getByRole("button", { name: /포켓 잔고 조회 기능 선택/ }).click();
  await page.getByRole("button", { name: "조회 실행" }).click();
  await expect(page.getByRole("table", { name: "계정 잔고 결과" })).toContainText("KRW");
  await expect(page.getByRole("table", { name: "계정 잔고 결과" })).toContainText("120,000 ￦");
  await page.getByRole("button", { name: "원본 추적 열기" }).click();
  await expect(page.getByRole("dialog", { name: "API 원본 추적" })).toContainText("trace_id");
  expect(upstreamRequests).toEqual([]);
  await expect(page.locator("body")).not.toContainText(/secret|authorization|bearer|jwt/i);
});

test("실제 주문은 폼·미리보기만 제공하고 게이트웨이와 상향 호출이 모두 0이다", async ({ page }) => {
  await page.goto(route);
  await page.getByRole("tab", { name: /주문/ }).click();
  await page.getByRole("button", { name: /주문 생성 기능 선택/ }).click();
  await page.getByLabel("거래쌍(market)").fill("krw-btc");

  await expect(page.getByRole("alert")).toContainText("업비트로 전송하지 않습니다");
  await expect(page.getByRole("region", { name: "최종 요청 미리보기" })).toContainText("rest.new-order");
  await expect(page.getByRole("region", { name: "최종 요청 미리보기" })).toContainText("KRW-BTC");
  await expect(page.getByRole("button", { name: "정책으로 전송 차단됨" })).toBeDisabled();
  expect(await harnessCounts(page)).toEqual({ gatewayCalls: 0, upstreamCalls: 0 });
});

test("공식 주문 테스트는 확인 대화상자 없이 가짜 상향에 한 번만 전송한다", async ({ page }) => {
  await page.goto(route);
  await page.getByRole("tab", { name: /주문/ }).click();
  await page.getByRole("button", { name: /주문 생성 테스트 기능 선택/ }).click();
  await page.getByLabel("거래쌍(market)").fill("KRW-BTC");
  await page.getByLabel("주문 방향(side)").selectOption("bid");
  await page.getByLabel("가격(price)").fill("1000");
  await page.getByLabel("주문 유형(ord_type)").selectOption("price");
  await page.getByRole("button", { name: "주문 테스트 실행" }).click();

  await expect(page.getByRole("button", { name: "원본 추적 열기" })).toBeVisible();
  await expect(page.getByText("비파괴 테스트")).toBeVisible();
  await expect(page.getByRole("dialog", { name: /확인/ })).toHaveCount(0);
  expect(await harnessCounts(page)).toEqual({ gatewayCalls: 1, upstreamCalls: 1 });
});

test("400·401·418·422·429·5xx 오류를 비밀값 없이 설명한다", async ({ page }) => {
  await page.goto(route);
  await page.getByRole("tab", { name: /계정/ }).click();
  await page.getByRole("button", { name: /포켓 잔고 조회 기능 선택/ }).click();
  const cases = [
    [400, "요청 값을 확인"],
    [401, "권한과 허용 IP"],
    [418, "일시 차단"],
    [422, "입력 형식"],
    [429, "요청 수 제한"],
    [500, "게이트웨이 또는 업비트 서버"]
  ] as const;
  for (const [status, message] of cases) {
    await page.evaluate((nextStatus) => {
      (window as unknown as { __exchangeHarness: { nextStatus: number } }).__exchangeHarness.nextStatus = nextStatus;
    }, status);
    await page.getByRole("button", { name: "조회 실행" }).click();
    await expect(page.getByRole("alert")).toContainText(message);
    await page.getByRole("button", { name: "원본 추적 열기" }).click();
    await expect(page.getByRole("dialog", { name: "API 원본 추적" })).toContainText(`\"status_code\": ${status}`);
    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: "원본 추적 열기" })).toBeFocused();
    await expect(page.locator("body")).not.toContainText("UNSAFE_DETAIL_MUST_NOT_RENDER");
  }
});

test("대체 필수 입력 조합을 안내하고 누락된 조회는 게이트웨이 전에 차단한다", async ({ page }) => {
  await page.goto(route);
  await page.getByRole("tab", { name: /주문/ }).click();
  await page.getByRole("button", { name: /개별 주문 조회 기능 선택/ }).click();
  await expect(page.getByText(/uuid 또는 identifier/)).toBeVisible();

  await page.getByRole("button", { name: "조회 실행" }).click();

  await expect(page.getByRole("alert")).toContainText("필수 입력 조합");
  expect(await harnessCounts(page)).toEqual({ gatewayCalls: 0, upstreamCalls: 0 });
});

test("상호 배타 주문 상태를 함께 입력하면 게이트웨이 전에 차단한다", async ({ page }) => {
  await page.goto(route);
  await page.getByRole("tab", { name: /주문/ }).click();
  await page.getByRole("button", { name: /체결 대기 주문 목록 조회 기능 선택/ }).click();
  await page.getByLabel("상태(state)").fill("wait");
  await page.getByLabel("상태 목록(states[])").fill("watch");

  await page.getByRole("button", { name: "조회 실행" }).click();

  await expect(page.getByRole("alert")).toContainText("동시에 사용할 수 없습니다");
  expect(await harnessCounts(page)).toEqual({ gatewayCalls: 0, upstreamCalls: 0 });
});

test("자격 증명 부재와 503을 서버 설정 문제로 안내한다", async ({ page }) => {
  await page.goto(`${route}?credentials=absent`);
  await expect(page.getByRole("status", { name: "자격 증명 상태" })).toContainText("서버 미설정");
  await page.getByRole("tab", { name: /계정/ }).click();
  await page.getByRole("button", { name: /포켓 잔고 조회 기능 선택/ }).click();
  await page.getByRole("button", { name: "조회 실행" }).click();
  await expect(page.getByRole("alert")).toContainText("서버에 API Key가 설정되지 않았습니다");
  await expect(page.locator('input[type="password"]')).toHaveCount(0);
});

test("390px에서 본문 가로 넘침 없이 키보드로 탭과 기능을 탐색한다", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(route);
  await page.getByRole("tab", { name: /포켓/ }).click();
  await page.getByRole("button", { name: /포켓별 API Key 목록 조회 기능 선택/ }).click();
  const includeExpired = page.getByLabel("만료 항목 포함(include_expired)");
  const includeExpiredBox = await includeExpired.boundingBox();
  expect(includeExpiredBox?.width).toBeLessThanOrEqual(20);
  const includeExpiredLabelBox = await includeExpired.locator("xpath=ancestor::label").boundingBox();
  expect(includeExpiredLabelBox?.height ?? 0).toBeGreaterThanOrEqual(44);
  await includeExpired.focus();
  await page.keyboard.press("Space");
  await expect(includeExpired).toBeChecked();

  const hasOverflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  expect(hasOverflow).toBe(false);

  await page.getByRole("tab", { name: /주문/ }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: /출금/ })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tab", { name: /출금/ })).toBeFocused();
  await page.keyboard.press("ArrowLeft");
  await expect(page.getByRole("tab", { name: /주문/ })).toBeFocused();
  await page.getByRole("button", { name: /주문 생성 테스트 기능 선택/ }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByText("비파괴 테스트")).toBeVisible();
});

async function harnessCounts(page: Page) {
  return page.evaluate(() => {
    const state = (window as unknown as {
      __exchangeHarness: { gatewayCalls: number; upstreamCalls: number };
    }).__exchangeHarness;
    return { gatewayCalls: state.gatewayCalls, upstreamCalls: state.upstreamCalls };
  });
}
