import { expect, test } from "@playwright/test";

const apiBaseUrl = process.env.E2E_API_BASE_URL ?? "http://127.0.0.1:18000";
const operatorToken = process.env.E2E_OPERATOR_TOKEN ?? "local-dev-token";

test("M1 운영 화면에서 주요 시나리오를 탐색한다", async ({ page, request }) => {
  const runtimeIssues: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error" || message.type() === "warning") {
      runtimeIssues.push(`[${message.type()}] ${message.text()}`);
    }
  });
  page.on("pageerror", (error) => {
    runtimeIssues.push(`[pageerror] ${error.message}`);
  });

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
  await expect(page.locator("#root")).not.toHaveText("운영 상태를 불러오는 중");
  await expect(page.getByText("데이터 수집관리", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "운영 상태" })).toBeVisible();
  await expect(page.getByRole("button", { name: "코인 상세" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "CSV 내보내기" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "운영 변경 저장" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "업비트 수집 운영 상태" })).toBeVisible();
  await expect(page.locator(".app-shell")).toHaveAttribute("data-theme", "dark");
  await expect(page.locator(".ops-summary-card").filter({ hasText: "worker 현황" })).toBeVisible();
  await expect(page.getByText("Realtime worker")).toBeVisible();
  await expect(page.getByText("Backfill worker")).toBeVisible();
  await expect(page.getByText(/동작중 코인 [0-9]+\/[0-9]+개/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "코인별 수집 상태" })).toBeVisible();
  await expect(page.locator(".dashboard-row-button").first()).toBeVisible();
  await expect(page.getByText("실시간 / 백필 row")).toBeVisible();
  await expect(page.getByText("상태", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("최신성", { exact: true })).toBeVisible();
  await expect(page.getByText("수집 커버리지", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("저장 행", { exact: true })).toBeVisible();
  await expect(page.getByText(/24H 거래대금/).first()).toBeVisible();
  await expect(page.getByText("최근 1분 수집 건수")).toBeVisible();
  await expect(page.getByRole("heading", { name: "구간형 수집 진행 상태" })).toBeVisible();
  await expect(page.getByLabel("실시간 정보 수집 현황 히트맵")).toBeVisible();
  await expect(page.getByText("오늘 저장 Row Count")).toBeVisible();
  await expect(page.getByRole("heading", { name: "운영 헬스" })).toBeVisible();
  await expect(page.getByText("Rate limit 여유 64%")).toHaveCount(0);
  await expect(page.getByText("중복 행 0")).toHaveCount(0);
  await expect(page.getByText("표시 KST")).toBeVisible();
  await expect(page.getByText("저장 UTC")).toBeVisible();
  await page.getByRole("button", { name: "Realtime worker 24시간 오류 상세" }).click();
  await expect(page.getByRole("dialog", { name: "Realtime worker 오류 상세" })).toBeVisible();
  await page.getByLabel("닫기").click();

  await page.locator(".dashboard-row-button").first().click();
  await expect(page.getByText(/코인별 수집 계획/)).toBeVisible();
  await expect(page.getByText("수집 시작 KST")).toBeVisible();
  await expect(page.getByText("현재 (지속)")).toBeVisible();
  await expect(page.getByRole("button", { name: "수정" })).toBeVisible();
  await expect(page.locator(".coverage-bar").first()).toBeVisible();

  await page.getByRole("button", { name: "수집 대상 설정" }).click();
  await expect(page.getByRole("heading", { name: "후보 유니버스 상위 100개" })).toBeVisible();
  await expect(page.getByText("선택 50/50")).toBeVisible();
  await expect(page.getByText(firstInstrumentName)).toBeVisible();
  await page.getByPlaceholder("코인명 또는 심볼 검색").fill(firstInstrument.baseAsset);
  await expect(page.getByText(firstInstrumentName)).toBeVisible();
  await page.getByPlaceholder("코인명 또는 심볼 검색").fill("");
  await page.getByRole("combobox", { name: "후보 정렬" }).selectOption("quality");
  await expect(page.getByRole("combobox", { name: "후보 정렬" })).toHaveValue("quality");
  await expect(page.getByText(/대상 변경 [0-9]+건/)).toBeVisible();
  await expect(page.getByRole("button", { name: "백필 계획 생성" })).toBeEnabled();
  await page.getByRole("button", { name: "백필 계획 생성" }).click();
  await expect(page.getByRole("dialog", { name: "백필 계획 생성" })).toBeVisible();
  await expect(page.getByText("선택 코인 50개")).toBeVisible();
  await page.getByRole("button", { name: "확인" }).click();
  await expect(page.getByText(/계획 /).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "백필 계획 승인" })).toBeEnabled();
  await page.getByRole("button", { name: "백필 계획 승인" }).click();
  await expect(page.getByText(/승인된 작업/)).toBeVisible();
  await page.getByRole("checkbox").first().uncheck();
  await expect(page.getByText("선택 49/50")).toBeVisible();
  await page.getByRole("checkbox").first().check();
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByText("선택 50/50")).toBeVisible();

  await page.getByRole("button", { name: "시장 리스트" }).click();
  await expect(page.getByRole("heading", { name: "시장 리스트" })).toBeVisible();
  await expect(page.getByText("등락률", { exact: true })).toBeVisible();
  await expect(page.getByText("24시간 거래대금", { exact: true })).toBeVisible();
  await expect(page.locator(".market-row-button").first()).toBeVisible();
  expect(await page.locator(".market-row-button").count()).toBeGreaterThan(0);
  await page.locator(".market-row-button").first().click();

  await expect(page.getByRole("dialog", { name: "코인 상세" })).toBeVisible();
  await expect(page.locator(".detail-title")).toBeVisible();
  await expect(page.getByText("2026년 1월 1분봉")).toBeVisible();
  await expect(page.getByLabel("TradingView 캔들 차트")).toBeVisible();
  await expect(page.getByText("현재가 게이지")).toBeVisible();
  await expect(page.getByText("24H 변동금액")).toBeVisible();
  await expect(page.getByText("24H 거래량")).toBeVisible();
  await expect(page.getByRole("heading", { name: "수집 품질 이력" })).toBeVisible();
  await expect(page.locator(".modal-backdrop")).toBeVisible();

  await page.getByLabel("닫기").click();
  await page.getByRole("button", { name: "확장성 점검" }).click();
  await expect(page.getByRole("heading", { name: "확장성 점검" }).first()).toBeVisible();
  await expect(page.getByText("수평 확장", { exact: true })).toBeVisible();
  await expect(page.getByText("메시지 큐", { exact: true })).toBeVisible();
  await expect(page.getByText(/CPU|메모리|TPS|QPS/)).toHaveCount(0);
  expect(runtimeIssues).toEqual([]);
});
