import { expect, test } from "@playwright/test";

const apiBaseUrl = process.env.E2E_API_BASE_URL ?? "http://127.0.0.1:18000";
const operatorToken = process.env.E2E_OPERATOR_TOKEN ?? "local-dev-token";

test("업비트 Quotation 전체 작업대를 가짜 게이트웨이로 탐색한다", async ({ page }) => {
  const gatewayRequests: Array<Record<string, unknown>> = [];
  const liveUpbitRequests: string[] = [];
  const runtimeIssues: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") runtimeIssues.push(message.text());
  });
  page.on("pageerror", (error) => runtimeIssues.push(error.message));
  page.on("request", (request) => {
    if (request.url().includes("api.upbit.com")) liveUpbitRequests.push(request.url());
    if (request.url().includes("/upbit-gateway/v1/requests")) {
      gatewayRequests.push(request.postDataJSON() as Record<string, unknown>);
    }
  });
  await page.goto("/");

  await expect(page.getByRole("button", { name: "Quotation API 테스트" })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("button", { name: "Exchange API 테스트" })).toBeVisible();
  await expect(page.getByRole("button", { name: "WebSocket API 테스트" })).toBeVisible();
  await page.getByRole("button", { name: "Quotation API 테스트" }).click();
  await expect(page.getByLabel("Quotation API 작업대")).toBeVisible();
  await expect(page.getByText("활성 12개 · 사용 중단 1개")).toBeVisible();

  await page.getByLabel("상세 정보 포함").check();
  await page.getByRole("button", { name: "요청 실행" }).click();
  await expect(page.getByRole("cell", { name: "비트코인" })).toBeVisible();
  await page.getByRole("button", { name: "원본 응답과 API 출처 보기" }).click();
  await expect(page.getByRole("dialog", { name: "API 요청 추적" })).toContainText("remaining_sec");
  await expect(page.getByRole("link", { name: "Upbit 공식 문서" })).toHaveAttribute("href", /docs\.upbit\.com/);
  await page.getByRole("button", { name: "닫기" }).click();

  await page.getByRole("tab", { name: "캔들" }).click();
  await page.getByLabel("API 기능").selectOption({ label: "분 캔들 조회" });
  await page.getByLabel("unit").selectOption("5");
  await page.getByRole("button", { name: "요청 실행" }).click();
  await expect(page.getByLabel("업비트 API 캔들 차트")).toBeVisible();
  await expect(page.getByText("10개 캔들 · 가장자리 이동 시 연속 조회")).toBeVisible();
  const chart = page.getByLabel("업비트 API 캔들 차트");
  const chartBox = await chart.boundingBox();
  if (!chartBox) throw new Error("캔들 차트의 크기를 확인할 수 없습니다.");
  await page.mouse.move(chartBox.x + chartBox.width * 0.45, chartBox.y + chartBox.height * 0.45);
  await page.mouse.down();
  await page.mouse.move(chartBox.x + chartBox.width * 0.8, chartBox.y + chartBox.height * 0.45, { steps: 8 });
  await page.mouse.up();
  await expect.poll(() => gatewayRequests.filter((request) => {
    const parameters = request.parameters as Record<string, unknown> | undefined;
    return typeof parameters?.to === "string";
  }).length).toBe(1);
  await expect(page.getByText("20개 캔들 · 가장자리 이동 시 연속 조회")).toBeVisible();

  await page.getByLabel("to").fill("2026-07-15T09:20");
  await page.getByRole("button", { name: "요청 실행" }).click();
  await page.getByRole("button", { name: "미래 데이터 조회" }).click();
  await expect(page.getByText("최신 데이터 끝")).toBeVisible();
  await expect(page.getByText("10개 캔들 · 가장자리 이동 시 연속 조회")).toBeVisible();
  const historicalParameters = gatewayRequests.at(-2)?.parameters as Record<string, unknown>;
  const futureParameters = gatewayRequests.at(-1)?.parameters as Record<string, unknown>;
  expect(futureParameters.to).not.toBe(historicalParameters.to);
  await expect(page.getByRole("button", { name: "미래 데이터 조회" })).toBeDisabled();

  await page.getByRole("tab", { name: "현재가" }).click();
  await page.getByLabel("API 기능").selectOption({ label: "페어 단위 현재가 조회" });
  await page.getByRole("button", { name: "요청 실행" }).click();
  await expect(page.getByText("150,000,000")).toBeVisible();

  await page.getByRole("tab", { name: "호가" }).click();
  await page.getByLabel("API 기능").selectOption({ label: "호가 조회" });
  await page.getByRole("button", { name: "요청 실행" }).click();
  await expect(page.getByText(/150,001,000 \/ 0.2/)).toBeVisible();
  await page.getByLabel("API 기능").selectOption({ label: "호가 모아보기 단위 조회 · 사용 중단" });
  await expect(page.getByText(/사용 중단\(deprecated\) API/)).toBeVisible();

  const safeRequestCount = gatewayRequests.length;
  await page.getByRole("button", { name: "Exchange API 테스트" }).click();
  await expect(page.getByRole("main", { name: "Exchange API 작업대" })).toBeVisible();
  await expect(page.getByRole("tab", { name: /주문 11/ })).toBeVisible();
  await page.getByRole("tab", { name: /주문 11/ }).click();
  await page.getByRole("button", { name: /주문 생성 기능 선택/ }).click();
  await expect(page.getByRole("button", { name: "정책으로 전송 차단됨" })).toBeDisabled();
  expect(gatewayRequests).toHaveLength(safeRequestCount);
  await page.getByRole("button", { name: "WebSocket API 테스트" }).click();
  await expect(page.getByText("WebSocket API 모듈 연결 대기")).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.locator(".app-shell").evaluate((element) => element.scrollWidth <= element.clientWidth)).toBeTruthy();
  expect(liveUpbitRequests).toEqual([]);
  expect(runtimeIssues).toEqual([]);
});

test("관심 코인 분석 화면이 WebSocket 메시지로 실시간 정보를 표시한다", async ({ page }) => {
  const runtimeIssues: string[] = [];
  const analysisFrames: { direction: "sent" | "received"; message: Record<string, unknown> }[] = [];
  let analysisSocketCount = 0;
  page.on("console", (message) => {
    if (message.type() === "error") runtimeIssues.push(message.text());
  });
  page.on("pageerror", (error) => runtimeIssues.push(error.message));
  page.on("websocket", (socket) => {
    if (!socket.url().includes("/v1/realtime/analysis")) return;
    analysisSocketCount += 1;
    socket.on("framesent", (event) => {
      analysisFrames.push({ direction: "sent", message: JSON.parse(String(event.payload)) });
    });
    socket.on("framereceived", (event) => {
      analysisFrames.push({ direction: "received", message: JSON.parse(String(event.payload)) });
    });
  });

  const expectIndependentSnapshot = async (
    startIndex: number,
    unit: string,
    rangeDays: number,
    instrumentMarketCode?: string,
    expectedSubscriptionCount?: number
  ) => {
    await expect.poll(() => {
      const frames = analysisFrames.slice(startIndex);
      const hasSubscription = frames.some(({ direction, message }) =>
        direction === "sent" &&
        message.type === "analysis.subscribe" &&
        message.unit === unit &&
        message.rangeDays === rangeDays
      );
      const received = frames
        .filter(({ direction }) => direction === "received")
        .map(({ message }) => message);
      const types = new Set(received.map((message) => message.type));
      const hasInstrument = instrumentMarketCode === undefined || received.some((message) =>
        message.type === "analysis.instrument" &&
        (message.instrument as { marketCode?: string } | undefined)?.marketCode === instrumentMarketCode
      );
      return hasSubscription && hasInstrument && [
        "analysis.session",
        "analysis.instrument",
        "analysis.chart",
        "analysis.indicators",
        "analysis.market"
      ].every((type) => types.has(type)) && received.some((message) =>
        message.type === "analysis.chart" &&
        message.unit === unit &&
        ((message.candles as unknown[] | undefined)?.length ?? 0) > 0
      ) && received.some((message) =>
        message.type === "analysis.indicators" &&
        ((message.points as unknown[] | undefined)?.length ?? 0) > 0
      );
    }).toBe(true);

    const frames = analysisFrames.slice(startIndex);
    const subscriptions = frames.filter(({ direction, message }) =>
      direction === "sent" &&
      message.type === "analysis.subscribe" &&
      message.unit === unit &&
      message.rangeDays === rangeDays
    );
    if (expectedSubscriptionCount !== undefined) {
      expect(subscriptions).toHaveLength(expectedSubscriptionCount);
    }
    const received = frames
      .filter(({ direction }) => direction === "received")
      .map(({ message }) => message);
    const candles = received
      .filter((message) => message.type === "analysis.chart" && message.unit === unit)
      .flatMap((message) => message.candles as Array<{ startedAt: string; open: string }>);
    const indicatorPoints = received
      .filter((message) => message.type === "analysis.indicators")
      .flatMap((message) => message.points as Array<{ startedAt: string; ema20: string | null }>);
    const marketMessages = received.filter((message) => message.type === "analysis.market");
    const marketTradePrice = String(
      (marketMessages.at(-1)?.ticker as { tradePrice?: string } | undefined)?.tradePrice ?? ""
    );

    expect(candles.length).toBeGreaterThan(0);
    expect(indicatorPoints).toHaveLength(candles.length);
    expect(indicatorPoints.map((point) => point.startedAt)).toEqual(
      candles.map((candle) => candle.startedAt)
    );
    expect(indicatorPoints.every((point) => point.ema20 !== null)).toBeTruthy();
    expect(marketTradePrice).not.toBe("");
    return { candles, indicatorPoints, marketTradePrice };
  };

  await page.goto("/");
  await expect(page.getByRole("button", { name: "코인 분석" })).toBeVisible({ timeout: 60_000 });
  await page.getByRole("button", { name: "코인 분석" }).click();

  await expect(page.getByRole("heading", { name: "관심 코인 선택" })).toBeVisible();
  await expect(page.getByLabel("코인 분석 화면")).toBeVisible();
  await expect(page.getByLabel("코인 분석 화면").getByText("WebSocket 실시간")).toBeVisible();
  await expect(page.getByRole("button", { name: "일봉" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("button", { name: "1년" })).toHaveAttribute("aria-pressed", "true");
  await expectIndependentSnapshot(0, "1d", 365, "KRW-BTC");
  const initialAnalysisSocketCount = analysisSocketCount;
  await expect(page.getByLabel("코인 분석 캔들 차트")).toBeVisible();
  await expect(page.getByLabel("현재가 호가 체결")).toContainText("현재가");
  await expect(page.getByLabel("현재가 호가 체결")).toContainText("호가 요약");
  await expect(page.getByLabel("현재가 호가 체결")).toContainText("체결 흐름");

  const requestedUnits = [
    ["1분", "1m"],
    ["5분", "5m"],
    ["10분", "10m"],
    ["30분", "30m"],
    ["시봉", "1h"],
    ["주봉", "1w"],
    ["월봉", "1M"]
  ] as const;
  let monthlyOneYearSnapshot: Awaited<ReturnType<typeof expectIndependentSnapshot>> | undefined;
  for (const [label, unit] of requestedUnits) {
    const startIndex = analysisFrames.length;
    await page.getByRole("button", { name: label, exact: true }).click();
    const snapshot = await expectIndependentSnapshot(startIndex, unit, 365, undefined, 1);
    if (unit === "1M") monthlyOneYearSnapshot = snapshot;
    await expect(page.getByRole("button", { name: label, exact: true })).toHaveAttribute("aria-pressed", "true");
  }

  let startIndex = analysisFrames.length;
  await page.getByRole("button", { name: "3년" }).click();
  const monthlyThreeYearSnapshot = await expectIndependentSnapshot(
    startIndex, "1M", 1095, "KRW-BTC", 1
  );
  expect(monthlyOneYearSnapshot).toBeDefined();
  expect(monthlyThreeYearSnapshot.candles.length).toBeGreaterThan(
    monthlyOneYearSnapshot?.candles.length ?? 0
  );
  expect(monthlyThreeYearSnapshot.candles).toHaveLength(3);
  const threeYearStartedAt = monthlyThreeYearSnapshot.candles.map((candle) =>
    new Date(candle.startedAt).getTime()
  );
  expect(Math.min(...threeYearStartedAt)).toBeLessThan(Date.now() - 365 * 24 * 60 * 60 * 1000);
  expect(Math.max(...threeYearStartedAt)).toBeGreaterThan(Date.now() - 365 * 24 * 60 * 60 * 1000);
  expect(monthlyThreeYearSnapshot.candles.every((candle) => Number(candle.open) >= 1_000_000)).toBeTruthy();
  expect(monthlyThreeYearSnapshot.marketTradePrice).toBe("100000000.0000");
  await expect(page.getByRole("button", { name: "3년" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".analysis-chart-panel")).toContainText("3개 표시");

  startIndex = analysisFrames.length;
  await page.getByRole("button", { name: "ETH 분석" }).click();
  const ethSnapshot = await expectIndependentSnapshot(startIndex, "1M", 1095, "KRW-ETH", 1);
  expect(ethSnapshot.candles).toHaveLength(2);
  expect(ethSnapshot.candles.every((candle) => {
    const open = Number(candle.open);
    return open >= 2_000_000 && open < 3_000_000;
  })).toBeTruthy();
  expect(ethSnapshot.marketTradePrice).toBe("50000000.0000");
  await expect(page.getByRole("heading", { name: "ETH / KRW" })).toBeVisible();
  await expect(page.locator(".analysis-chart-panel")).toContainText("2개 표시");
  await expect(page.getByLabel("현재가 호가 체결")).toContainText("₩50,000,000");
  expect(analysisSocketCount).toBe(initialAnalysisSocketCount);
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
  await page.waitForTimeout(31_000);
  await page.goto("/");
  await page.getByRole("button", { name: "시스템 관리" }).click();

  await expect(page.getByRole("heading", { name: "시스템 관리" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "실시간 수집" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Backfill 수집" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "캔들 집계" })).toBeVisible();
  const aggregationCard = page.getByLabel("캔들 집계 워커");
  await expect(aggregationCard.getByText("집계 워커", { exact: true })).toBeVisible();
  await expect(aggregationCard.getByText("동작 중", { exact: true })).toBeVisible();
  await expect(aggregationCard.getByText(/마지막 heartbeat/)).toBeVisible();
  const aggregationCounts = aggregationCard.getByText(/^집계 작업 .* · 전체 \d+ · 완료 \d+ · 실행 \d+ · 대기 \d+ · 실패 \d+$/);
  await expect(aggregationCounts).toHaveText(
    "집계 작업 pending · 전체 350 · 완료 0 · 실행 0 · 대기 350 · 실패 0"
  );
  const countText = await aggregationCounts.innerText();
  const counts = countText.match(/전체 (\d+) · 완료 (\d+) · 실행 (\d+) · 대기 (\d+) · 실패 (\d+)/);
  expect(counts).not.toBeNull();
  const [, total, completed, running, pending, failed] = counts!.map(Number);
  expect(total).toBe(completed + running + pending + failed);
  expect(pending).toBe(350);
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
