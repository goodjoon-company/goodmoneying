import { expect, test } from "@playwright/test";

const apiBaseUrl = process.env.E2E_API_BASE_URL ?? "http://127.0.0.1:18000";
const operatorToken = process.env.E2E_OPERATOR_TOKEN ?? "local-dev-token";

test("업비트 API 테스트 화면에서 공개 캔들 차트와 보조지표를 조회한다", async ({ page }) => {
  const candleUrls: string[] = [];
  await page.route("https://api.upbit.com/v1/market/all**", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify([{ market: "KRW-BTC", korean_name: "비트코인", english_name: "Bitcoin", market_warning: "NONE" }])
    });
  });
  await page.route("https://api.upbit.com/v1/candles/**", async (route) => {
    const url = route.request().url();
    candleUrls.push(url);
    const isPastPage = url.includes("to=");
    const candles = Array.from({ length: 20 }, (_, index) => ({
      market: "KRW-BTC",
      candle_date_time_utc: isPastPage
        ? `2026-07-13T23:${String(59 - index).padStart(2, "0")}:00`
        : `2026-07-14T00:${String(19 - index).padStart(2, "0")}:00`,
      opening_price: (isPastPage ? 200 : 100) + index,
      high_price: (isPastPage ? 210 : 110) + index,
      low_price: (isPastPage ? 190 : 90) + index,
      trade_price: (isPastPage ? 205 : 105) + index,
      candle_acc_trade_volume: 10 + index,
      candle_acc_trade_price: 1000 + index
    }));
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(candles) });
  });
  await page.goto("/");

  await page.getByRole("button", { name: "업비트 API 테스트" }).click();
  await expect(page.getByLabel("업비트 API 테스트 화면")).toBeVisible({ timeout: 60_000 });
  await page.getByRole("tab", { name: "캔들" }).click();
  await expect(page.getByLabel("거래쌍", { exact: true })).toBeDisabled();
  await page.getByRole("tab", { name: "거래쌍 목록" }).click();
  await page.getByRole("button", { name: "거래쌍 목록 조회" }).click();
  await expect(page.getByRole("cell", { name: "비트코인" })).toBeVisible();
  await page.getByRole("tab", { name: "캔들" }).click();
  await page.getByLabel("거래쌍", { exact: true }).selectOption("KRW-BTC");
  await page.getByLabel("캔들 주기").selectOption("5m");
  await page.getByRole("button", { name: "캔들 조회" }).click();

  await expect(page.getByRole("heading", { name: "KRW-BTC 5분 캔들" })).toBeVisible();
  await expect(page.getByLabel("업비트 API 캔들 차트")).toBeVisible();
  await expect(page.getByLabel("최신 OHLCV와 보조지표")).toContainText("RSI 14");
  await expect(page.getByText("업비트 응답 20개 · 시간 오름차순")).toBeVisible();
  await expect(page.getByLabel("캔들 원본 JSON")).toContainText("trade_price");
  const chart = page.getByLabel("업비트 API 캔들 차트");
  const chartBox = await chart.boundingBox();
  if (!chartBox) throw new Error("캔들 차트의 크기를 확인할 수 없습니다.");
  await page.mouse.move(chartBox.x + chartBox.width * 0.45, chartBox.y + chartBox.height * 0.45);
  await page.mouse.down();
  await page.mouse.move(chartBox.x + chartBox.width * 0.8, chartBox.y + chartBox.height * 0.45, { steps: 8 });
  await page.mouse.up();
  await expect.poll(() => candleUrls.filter((url) => url.includes("to=")).length).toBe(1);
  await expect(page.getByText("업비트 응답 40개 · 시간 오름차순")).toBeVisible();
  await expect(page.getByText("최근 10개 OHLCV 표 보기")).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByLabel("업비트 캔들 조회 조건")).toBeVisible();
  await expect(page.getByLabel("업비트 API 캔들 차트")).toBeVisible();
  expect(await page.locator(".app-shell").evaluate((element) => element.scrollWidth <= element.clientWidth)).toBeTruthy();
});

test("관심 코인 분석 화면이 WebSocket 메시지로 실시간 정보를 표시한다", async ({ page }) => {
  const runtimeIssues: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") runtimeIssues.push(message.text());
  });
  page.on("pageerror", (error) => runtimeIssues.push(error.message));

  await page.goto("/");
  await expect(page.getByRole("button", { name: "코인 분석" })).toBeVisible({ timeout: 60_000 });
  await page.getByRole("button", { name: "코인 분석" }).click();

  await expect(page.getByRole("heading", { name: "관심 코인 선택" })).toBeVisible();
  await expect(page.getByLabel("코인 분석 화면")).toBeVisible();
  await expect(page.getByLabel("코인 분석 화면").getByText("WebSocket 실시간")).toBeVisible();
  await expect(page.getByRole("button", { name: "일봉" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("button", { name: "1년" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByLabel("코인 분석 캔들 차트")).toBeVisible();
  await expect(page.getByLabel("현재가 호가 체결")).toContainText("현재가");
  await expect(page.getByLabel("현재가 호가 체결")).toContainText("호가 요약");
  await expect(page.getByLabel("현재가 호가 체결")).toContainText("체결 흐름");

  await page.getByRole("button", { name: "월봉" }).click();
  await page.getByRole("button", { name: "3년" }).click();
  await expect(page.getByRole("button", { name: "월봉" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("button", { name: "3년" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText("주식 분석")).toHaveCount(0);
  expect(runtimeIssues).toEqual([]);
});

test("모바일에서도 코인 분석 메뉴와 분석 화면에 접근할 수 있다", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");

  await page.getByRole("button", { name: "코인 분석" }).click();
  await expect(page.getByLabel("코인 분석 화면")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("heading", { name: "관심 코인 선택" })).toBeVisible();
  await expect(page.getByLabel("코인 분석 캔들 차트")).toBeVisible();
});

test("시스템 관리 화면은 WebSocket으로 수집 대상과 집계 진행률을 표시한다", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "시스템 관리" }).click();

  await expect(page.getByRole("heading", { name: "시스템 관리" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "실시간 수집" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Backfill 수집" })).toBeVisible();
  await expect(page.getByText("자동 집계 테이블", { exact: true })).toBeVisible();
  await expect(page.getByText(/WebSocket (연결됨|재연결 중)/)).toBeVisible({ timeout: 60_000 });
  await expect(page.locator(".system-items").first()).toContainText("KRW-");

  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.locator(".app-shell").evaluate((element) => element.scrollWidth <= element.clientWidth)).toBeTruthy();
});

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
  expect(universe.entries).toHaveLength(100);
  expect(
    universe.entries.filter((entry: { selected: boolean }) => entry.selected)
  ).toHaveLength(50);
  const baselineEntries = universe.entries.slice(0, 50);
  const baselineTargetIds = baselineEntries.map(
    (entry: { instrument: { id: number } }) => entry.instrument.id
  );
  const firstInstrument = baselineEntries[0].instrument as {
    baseAsset: string;
    quoteCurrency: string;
  };
  const secondInstrument = baselineEntries[1].instrument as {
    baseAsset: string;
    quoteCurrency: string;
  };
  const firstInstrumentName = `${firstInstrument.baseAsset} / ${firstInstrument.quoteCurrency}`;
  const secondInstrumentName = `${secondInstrument.baseAsset} / ${secondInstrument.quoteCurrency}`;
  const pausedBackfillTargets = baselineEntries.slice(0, 5);
  const pausedBackfillTargetIds = pausedBackfillTargets.map(
    (entry: { instrument: { id: number } }) => entry.instrument.id
  );
  const pausedBackfillTargetSymbols = [...pausedBackfillTargets]
    .sort(
      (
        left: { instrument: { marketCode: string } },
        right: { instrument: { marketCode: string } }
      ) => left.instrument.marketCode.localeCompare(right.instrument.marketCode)
    )
    .map((entry: { instrument: { baseAsset: string } }) => entry.instrument.baseAsset)
    .join(", ");
  const resetResponse = await request.put(`${apiBaseUrl}/v1/collection-targets`, {
    headers: { "X-Operator-Token": operatorToken },
    data: {
      instrumentIds: baselineTargetIds,
      reason: "E2E baseline reset"
    }
  });
  expect(resetResponse.ok()).toBeTruthy();
  const backfillJobResponse = await request.post(`${apiBaseUrl}/v1/backfill/jobs`, {
    headers: { "X-Operator-Token": operatorToken },
    data: {
      dataType: "source_candle",
      targetStartAt: "2026-01-01T00:00:00+09:00",
      targetEndAt: "2026-01-03T00:00:00+09:00",
      instrumentIds: pausedBackfillTargetIds
    }
  });
  expect(backfillJobResponse.ok()).toBeTruthy();
  const pausedBackfillJob = await backfillJobResponse.json();
  const pauseBackfillResponse = await request.post(
    `${apiBaseUrl}/v1/backfill/jobs/${pausedBackfillJob.id}/pause`,
    {
      headers: { "X-Operator-Token": operatorToken }
    }
  );
  expect(pauseBackfillResponse.ok()).toBeTruthy();

  await page.goto("/");

  await expect(page.getByText("goodmoneying", { exact: true }).first()).toBeVisible({
    timeout: 60_000
  });
  await expect(page.locator("#root")).not.toHaveText("운영 상태를 불러오는 중");
  await expect(page.getByLabel("제품 메뉴").getByRole("button").first()).toHaveText(/관심종목/);
  await expect(page.getByText("데이터 수집관리", { exact: true })).toBeVisible();
  await expect(
    page.locator(".product-nav section").filter({ hasText: "데이터 수집관리" })
  ).not.toContainText("관심종목");
  await expect(page.getByRole("button", { name: "운영 상태" })).toBeVisible();
  await expect(page.getByRole("button", { name: "코인 상세" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "CSV 내보내기" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "운영 변경 저장" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "관심 코인 50개 보기" })).toBeVisible();
  await page.getByRole("button", { name: "관심 코인 50개 보기" }).click();
  await expect(page.getByRole("dialog", { name: "관심 코인 목록" })).toBeVisible();
  await expect(page.getByRole("dialog", { name: "관심 코인 목록" })).toContainText("50개");
  await expect(page.getByRole("dialog", { name: "관심 코인 목록" })).toContainText(
    firstInstrumentName
  );
  await page.getByLabel("닫기").click();
  await expect(page.getByRole("heading", { name: "업비트 수집 운영 상태" })).toBeVisible();
  await expect(page.locator(".app-shell")).toHaveAttribute("data-theme", "dark");
  await expect(page.locator(".ops-summary-card").filter({ hasText: "worker 현황" })).toBeVisible();
  await expect(page.getByText("Realtime worker")).toBeVisible();
  await expect(page.getByText("Backfill worker")).toBeVisible();
  await expect(page.getByLabel(/Realtime worker 24시간 수집 [0-9,]+ rows/)).toBeVisible();
  await expect(page.getByText(/동작중 코인 [0-9]+\/[0-9]+개/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "코인별 수집 상태" })).toBeVisible();
  const dashboardRows = page.locator(".ops-coin-table .dashboard-row-button");
  await expect(dashboardRows).toHaveCount(50);
  await expect(dashboardRows.first()).toBeVisible();
  await expect(page.getByText("실시간 / 백필 row")).toBeVisible();
  await expect(page.getByText("상태", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("최신성", { exact: true })).toBeVisible();
  await expect(page.getByText("수집 커버리지", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("저장 행", { exact: true })).toBeVisible();
  const tradeSortButton = page.getByRole("button", { name: /24H 거래대금/ });
  await expect(tradeSortButton).toBeVisible();
  const firstDashboardRowBeforeSort = await dashboardRows.first().innerText();
  await tradeSortButton.click();
  await expect
    .poll(async () => dashboardRows.first().innerText())
    .not.toBe(firstDashboardRowBeforeSort);
  await expect(page.getByText("최근 1분 수집 건수")).toBeVisible();
  await expect(page.getByRole("heading", { name: "구간형 수집 진행 상태" })).toBeVisible();
  await expect(page.getByLabel("실시간 체결 빈도 히트맵")).toBeVisible();
  await expect(page.getByText("오늘 저장 Row Count")).toBeVisible();
  await expect(page.getByRole("heading", { name: "운영 헬스" })).toBeVisible();
  await expect(page.getByText("Rate limit 여유 64%")).toHaveCount(0);
  await expect(page.getByText("중복 행 0")).toHaveCount(0);
  await expect(page.getByText("표시 KST")).toBeVisible();
  await expect(page.getByText("저장 KST")).toBeVisible();
  await expect(page.getByText("SSE 실시간")).toBeVisible();
  await page.getByRole("button", { name: "Realtime worker 24시간 오류 상세" }).click();
  await expect(page.getByRole("dialog", { name: "Realtime worker 오류 상세" })).toBeVisible();
  await page.getByLabel("닫기").click();

  await page.locator(".dashboard-row-button").first().click();
  await expect(page.getByText(/코인별 수집 계획/)).toBeVisible();
  await expect(page.getByText("수집 시작 KST")).toBeVisible();
  await expect(page.getByText("현재 (지속)")).toBeVisible();
  await expect(page.getByRole("button", { name: "수정" })).toBeVisible();
  await expect(page.locator(".coverage-bar").first()).toBeVisible();

  await page.getByRole("button", { name: "Backfill 관리" }).click();
  await expect(page.getByRole("heading", { name: "수집 후보군 상위 100개" })).toBeVisible();
  await expect(page.getByText("선택 50/50")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("24시간 거래대금")).toBeVisible();
  await expect(page.getByText("수집 시작일")).toBeVisible();
  await expect(page.getByText("수집 최종일")).toBeVisible();
  await expect(page.getByText("실시간").first()).toBeVisible();
  await expect(page.getByText("품질")).toHaveCount(0);
  await expect(page.getByText(firstInstrumentName)).toBeVisible();
  await page.getByPlaceholder("코인명 또는 심볼 검색").fill(firstInstrument.baseAsset);
  await expect(page.getByText(firstInstrumentName)).toBeVisible();
  await page.getByPlaceholder("코인명 또는 심볼 검색").fill("");
  await expect(page.getByRole("combobox", { name: "후보 정렬" })).toHaveValue("trade");
  await expect(page.getByText(/대상 변경 [0-9]+건/)).toBeVisible();
  await expect(page.getByRole("button", { name: "백필 계획 생성" })).toBeEnabled();
  await expect(page.getByText(`작업 ${pausedBackfillJob.id}`)).toBeVisible();
  const pausedBackfillCard = page
    .locator(".approved-backfill-card")
    .filter({ hasText: `작업 ${pausedBackfillJob.id}` });
  await expect(pausedBackfillCard.getByText("일시정지", { exact: true })).toBeVisible();
  await expect(page.getByText(/결측 구간 처리/).first()).toBeVisible();
  await expect(page.getByLabel(`작업 ${pausedBackfillJob.id} 대상 전체 보기`)).toBeVisible();
  const pausedBackfillSummary = pausedBackfillCard.getByText(/외 1개/);
  await expect(pausedBackfillSummary).toHaveAttribute("title", pausedBackfillTargetSymbols);
  await expect(
    page.getByRole("button", { name: `작업 ${pausedBackfillJob.id} 재개` })
  ).toBeVisible();
  await page.getByRole("button", { name: `작업 ${pausedBackfillJob.id} 재개` }).click();
  await expect
    .poll(async () => {
      const jobsResponse = await request.get(`${apiBaseUrl}/v1/backfill/jobs`);
      expect(jobsResponse.ok()).toBeTruthy();
      const jobs = (await jobsResponse.json()) as { items: Array<{ id: number; status: string }> };
      return jobs.items.find((job) => job.id === pausedBackfillJob.id)?.status;
    })
    .toMatch(/^(running|succeeded)$/);
  await page.getByRole("button", { name: "백필 계획 생성" }).click();
  await expect(page.getByRole("dialog", { name: "백필 계획 생성" })).toBeVisible();
  await expect(page.getByText("선택 코인 50개")).toBeVisible();
  await page.getByRole("button", { name: "백필 시작" }).click();
  await expect(page.getByRole("button", { name: "백필 계획 승인" })).toHaveCount(0);
  await page.getByRole("checkbox").first().uncheck();
  await expect(page.getByText("선택 49/50")).toBeVisible();
  await page.getByRole("checkbox").first().check();
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByText("선택 50/50")).toBeVisible();

  await expect(page.getByRole("button", { name: "시장 리스트" })).toHaveCount(0);
  await page.getByRole("button", { name: "관심종목" }).click();
  await expect(page.getByRole("heading", { name: "관심종목" }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "코인", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "주식", exact: true })).toHaveCount(0);
  await expect(page.getByPlaceholder("종목명 또는 심볼 검색")).toBeVisible();
  await expect(page.getByText("관심추가 항목", { exact: true })).toBeVisible();
  await expect(page.getByText("후보 종목", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /관심 추가 정렬/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /등락률 .* KST 기준 정렬/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /24시간 거래대금 정렬/ })).toHaveAttribute(
    "aria-sort",
    "descending"
  );
  await expect(page.getByRole("button", { name: /기준일시 정렬/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /캔들 커버리지 정렬/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /1분 캔들 수 정렬/ })).toBeVisible();
  await expect(page.getByText("품질", { exact: true })).toHaveCount(0);
  await expect(page.getByText("KRW").first()).toBeVisible();
  await page.getByPlaceholder("종목명 또는 심볼 검색").fill(firstInstrument.baseAsset);
  await expect(page.getByText(firstInstrumentName)).toBeVisible();
  await page.getByPlaceholder("종목명 또는 심볼 검색").fill("");
  await expect(page.locator(".market-row-button").first()).toBeVisible();
  expect(await page.locator(".market-row-button").count()).toBeGreaterThan(0);
  const firstMarketRow = page.locator(".table-row").filter({ hasText: firstInstrumentName });
  await expect(firstMarketRow).toContainText("2026. 01. 01.");
  await page.getByRole("button", { name: `${secondInstrument.baseAsset} 관심 순서 위로` }).click();
  await expect(page.locator(".market-row-button").first()).toContainText(secondInstrumentName);
  await page.getByRole("button", { name: "관심 코인 50개 보기" }).click();
  const reorderedFavoriteDialog = page.getByRole("dialog", { name: "관심 코인 목록" });
  await expect(reorderedFavoriteDialog.locator(".favorite-coin-item").first()).toContainText(
    secondInstrumentName
  );
  await page.getByLabel("닫기").click();
  await page.getByRole("button", { name: "Backfill 관리" }).click();
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect
    .poll(async () => {
      const marketListResponse = await request.get(`${apiBaseUrl}/v1/market-list`);
      expect(marketListResponse.ok()).toBeTruthy();
      const marketList = await marketListResponse.json();
      return marketList.rows[0].instrument.baseAsset;
    })
    .toBe(secondInstrument.baseAsset);
  await page.getByRole("button", { name: "관심종목" }).click();
  await page.getByRole("button", { name: /관심 추가 정렬/ }).click();
  await expect(page.locator(".market-row-button").first()).toContainText(secondInstrumentName);
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
  await expect(page.getByRole("button", { name: "확장성 점검" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "확장성 점검" })).toHaveCount(0);
  expect(runtimeIssues).toEqual([]);
});
