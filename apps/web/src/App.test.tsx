import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import {
  createTestDashboardSummary,
  createTestInstruments,
  createTestMarketRows,
  createTestOperationsFetch
} from "./testOperationsApi";

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(createTestOperationsFetch()));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("데이터 수집관리 화면", () => {
  it("관심 코인만 선택하는 코인 분석 메뉴를 제공한다", async () => {
    const user = userEvent.setup();
    render(<App />);

    await screen.findByRole("button", { name: "코인 분석" });
    await user.click(screen.getByRole("button", { name: "코인 분석" }));

    expect(await screen.findByRole("heading", { name: "코인 분석" })).toBeInTheDocument();
    expect(screen.getByText("관심 코인 선택")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /BTC.*분석/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "일봉" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "1년" })).toBeInTheDocument();
    expect(screen.getByLabelText("현재가 호가 체결")).toBeInTheDocument();
    expect(screen.queryByText("주식 분석")).not.toBeInTheDocument();
    expect(screen.queryByText("국내 주식 리스트")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^주식$/ })).not.toBeInTheDocument();
  });

  it("업비트 API 2레벨 메뉴와 게이트웨이 기반 Quotation 작업대를 표시한다", async () => {
    const operationsFetch = createTestOperationsFetch();
    const upbitFetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/upbit-gateway/v1/catalog") return Response.json({
        catalog_version: "1.6.3", verified_at: "2026-07-16",
        official_baseline: "https://docs.upbit.com/kr/llms.txt",
        rest_endpoints: [
          {
            endpoint_id: "rest.list-trading-pairs", title: "페어 목록 조회", category: "quotation",
            functional_group: "pair", method: "GET", path: "/v1/market/all",
            parameters: [{ name: "is_details", location: "query", type: "boolean", required: false }],
            rate_limit_group: "market", safety: "read",
            source_url: "https://docs.upbit.com/kr/reference/list-trading-pairs"
          },
          {
            endpoint_id: "rest.get-balance", title: "포켓 잔고 조회", category: "exchange",
            functional_group: "asset", method: "GET", path: "/v1/accounts", parameters: [],
            rate_limit_group: "exchange-default", safety: "read",
            source_url: "https://docs.upbit.com/kr/reference/get-balance"
          }
        ]
      });
      if (url === "/upbit-gateway/health") return Response.json({
        status: "ok", service: "upbit-gateway", catalog_version: "1.6.3",
        credentials_configured: false
      });
      if (url === "/upbit-gateway/v1/requests") return Response.json({
        trace_id: "3cb59f4b-49b4-4b7d-951a-00f015bedee9", endpoint_id: "rest.list-trading-pairs",
        request: { method: "GET", path: "/v1/market/all", parameters: { is_details: true } },
        response: { status_code: 200, body: [{ market: "KRW-BTC", korean_name: "비트코인", english_name: "Bitcoin" }] },
        rate_limit: { group: "market", remaining_sec: 9, retry_after: null },
        duration_ms: 12.4, received_at: "2026-07-16T00:00:00Z"
      });
      return operationsFetch(input, init);
    });
    vi.stubGlobal("fetch", upbitFetch);
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByRole("button", { name: "Quotation API 테스트" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Exchange API 테스트" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "WebSocket API 테스트" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Quotation API 테스트" }));
    expect(await screen.findByLabelText("Quotation API 작업대")).toBeInTheDocument();
    await user.click(screen.getByLabelText("상세 정보 포함(is_details)"));
    await user.click(screen.getByRole("button", { name: "요청 실행" }));
    expect(await screen.findByRole("cell", { name: "비트코인" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Exchange API 테스트" }));
    expect(await screen.findByRole("main", { name: "Exchange API 작업대" })).toBeInTheDocument();
    await waitFor(() => expect(upbitFetch.mock.calls.map(([input]) => String(input)))
      .toContain("/upbit-gateway/health"));
    await waitFor(() => expect(screen.getByRole("status", { name: "자격 증명 상태" }))
      .toHaveTextContent("서버 미설정"));
    expect(screen.queryByText("Exchange API 모듈 연결 대기")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "WebSocket API 테스트" }));
    expect(await screen.findByLabelText("업비트 웹소켓 작업대")).toBeInTheDocument();
    expect(screen.getByLabelText("공개 연결 상태")).toHaveTextContent("closed");
    expect(screen.getByLabelText("비공개 연결 상태")).toHaveTextContent("closed");
    expect(screen.queryByLabelText("페어")).not.toBeInTheDocument();
    expect(screen.queryByText("WebSocket API 모듈 연결 대기")).not.toBeInTheDocument();
    expect(JSON.stringify(upbitFetch.mock.calls)).not.toContain("https://api.upbit.com");
  });

  it("좌측 내비게이션과 운영 상태 대시보드를 첫 화면으로 표시한다", async () => {
    const { container } = render(<App />);

    expect(await screen.findByText("goodmoneying")).toBeInTheDocument();
    const productMenu = screen.getByLabelText("제품 메뉴");
    expect(within(productMenu).getAllByRole("button")[0]).toHaveAccessibleName("관심종목");
    expect(await screen.findByText("데이터 수집관리")).toBeInTheDocument();
    const collectionGroup = screen.getByRole("heading", { name: "데이터 수집관리" })
      .parentElement;
    expect(collectionGroup).not.toBeNull();
    expect(within(collectionGroup as HTMLElement).queryByRole("button", { name: "관심종목" }))
      .not.toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "운영 상태" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "코인 상세" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "CSV 내보내기" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "운영 변경 저장" })).not.toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "업비트 수집 운영 상태" })).toBeInTheDocument();
    expect(await screen.findByText("worker 현황")).toBeInTheDocument();
    expect(await screen.findByText("Realtime worker")).toBeInTheDocument();
    expect(await screen.findByText("Backfill worker")).toBeInTheDocument();
    const favoriteSummary = await screen.findByRole("button", { name: "관심 코인 50개 보기" });
    expect(favoriteSummary).toBeInTheDocument();
    await userEvent.setup().click(favoriteSummary);
    const favoriteDialog = await screen.findByRole("dialog", { name: "관심 코인 목록" });
    expect(favoriteDialog).toBeInTheDocument();
    expect(within(favoriteDialog).getByText("BTC / KRW")).toBeInTheDocument();
    expect(within(favoriteDialog).getByText("GM050 / KRW")).toBeInTheDocument();
    expect(within(favoriteDialog).queryByText("GM051 / KRW")).not.toBeInTheDocument();
    await userEvent.setup().click(within(favoriteDialog).getByRole("button", { name: "닫기" }));
    expect(screen.getAllByText("BTC / KRW")[0]).toBeInTheDocument();
    expect(screen.getByText("코인별 수집 상태")).toBeInTheDocument();
    expect(screen.getByText("운영 헬스")).toBeInTheDocument();
    expect(screen.getByText(/마지막 갱신/)).toBeInTheDocument();
    expect(screen.getByText("표시 KST")).toBeInTheDocument();
    expect(screen.getByText("저장 KST")).toBeInTheDocument();
    expect(screen.getByText("SSE 실시간")).toBeInTheDocument();
    expect(container.querySelector(".app-shell")).toHaveAttribute("data-theme", "dark");
  });

  it("운영 상태는 코인별 실시간 수집과 수집 범위를 동적인 숫자로 표시한다", async () => {
    const user = userEvent.setup();
    render(<App />);

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "업비트 수집 운영 상태" })).toBeInTheDocument()
    );

    expect(screen.getByText("최근 1분 수집 건수")).toBeInTheDocument();
    expect(screen.getByText("실시간 / 백필 row")).toBeInTheDocument();
    expect(screen.getAllByText("오늘 저장 Row Count")[0]).toBeInTheDocument();
    expect(screen.getByText("구간형 수집 진행 상태")).toBeInTheDocument();
    expect(screen.getByText("상태")).toBeInTheDocument();
    expect(screen.getByText("최신성")).toBeInTheDocument();
    expect(screen.getAllByText("수집 커버리지")[0]).toBeInTheDocument();
    expect(screen.getByText("저장 행")).toBeInTheDocument();
    expect(screen.getAllByText(/24H 거래대금/)[0]).toBeInTheDocument();
    expect(screen.getByLabelText("Realtime worker 24시간 수집 450 rows")).toBeInTheDocument();
    expect(screen.getByText("24시간 오류 2건")).toBeInTheDocument();
    expect(screen.getByText("전체 오류 1건")).toBeInTheDocument();
    expect(screen.getByText("동작중 코인 1/3개")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /BTC \/ KRW/ }));

    expect(await screen.findByText(/코인별 수집 계획/)).toBeInTheDocument();
    expect(screen.getByText("수집 시작 KST")).toBeInTheDocument();
    expect(screen.getByText("현재 (지속)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "수정" })).toBeInTheDocument();
    expect(screen.getAllByText("캔들")[0]).toBeInTheDocument();
    expect(screen.getAllByText("현재가")[0]).toBeInTheDocument();
    expect(screen.getByText(/구간형 진행 상태/)).toBeInTheDocument();
    expect(document.querySelector(".coverage-bar")).toBeInTheDocument();
  });

  it("운영 상태의 코인별 수집 상태는 전체 대상 50개를 표시하고 24H 거래대금 헤더로 정렬한다", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        createTestOperationsFetch({
          dashboard: createTestDashboardSummary()
        })
      )
    );
    render(<App />);

    await screen.findByRole("heading", { name: "코인별 수집 상태" });

    const rows = () => Array.from(document.querySelectorAll(".ops-coin-table .dashboard-row-button"));
    expect(rows()).toHaveLength(50);
    expect(rows()[0]).toHaveTextContent("BTC / KRW");
    expect(rows()[49]).toHaveTextContent("GM050 / KRW");

    await user.click(screen.getByRole("button", { name: /24H 거래대금/ }));

    expect(rows()[0]).toHaveTextContent("GM050 / KRW");
    expect(rows()[49]).toHaveTextContent("BTC / KRW");
  });

  it("worker 현황판에서 수집 오류 상세를 레이어 팝업으로 표시한다", async () => {
    const user = userEvent.setup();
    render(<App />);

    await screen.findByRole("heading", { name: "업비트 수집 운영 상태" });
    await user.click(screen.getByRole("button", { name: "Realtime worker 24시간 오류 상세" }));

    expect(await screen.findByRole("dialog", { name: "Realtime worker 오류 상세" })).toBeInTheDocument();
    expect(screen.getByText("UpbitTimeout")).toBeInTheDocument();
    expect(screen.getByText("현재가 수집 요청 시간이 초과되었습니다.")).toBeInTheDocument();

    await user.click(screen.getByLabelText("닫기"));
    await user.click(screen.getByRole("button", { name: "Backfill worker 전체 오류 상세" }));

    expect(await screen.findByRole("dialog", { name: "Backfill worker 오류 상세" })).toBeInTheDocument();
    expect(screen.getByText("UpbitBackfillError")).toBeInTheDocument();
    expect(screen.getByText("백필 캔들 조회 실패")).toBeInTheDocument();
  });

  it("worker 상태를 클릭하면 동작 진단 정보를 레이어 팝업으로 표시한다", async () => {
    const user = userEvent.setup();
    render(<App />);

    await screen.findByRole("heading", { name: "업비트 수집 운영 상태" });
    await user.click(screen.getByRole("button", { name: "Backfill worker 상태 상세: 동작 중" }));

    const dialog = await screen.findByRole("dialog", { name: "Backfill worker 동작 상세" });
    expect(dialog).toBeInTheDocument();
    expect(within(dialog).getByText("마지막 heartbeat")).toBeInTheDocument();
    expect(within(dialog).getByText(/09:00/)).toBeInTheDocument();
    expect(within(dialog).queryByText("2026-06-19T00:00:00.000Z")).not.toBeInTheDocument();
    expect(within(dialog).getByText("최근 heartbeat 정상")).toBeInTheDocument();
    expect(within(dialog).getByText("동작중 코인")).toBeInTheDocument();
    expect(
      within(dialog).getByText("현재 실행 중인 백필 계획의 running 대상 수")
    ).toBeInTheDocument();
  });

  it("Backfill 관리는 최대 50개 후보 선택을 저장한다", async () => {
    const user = userEvent.setup();
    render(<App />);

    await screen.findByRole("heading", { name: "업비트 수집 운영 상태" });
    await user.click(screen.getByRole("button", { name: "Backfill 관리" }));
    expect(await screen.findByText("수집 후보군 상위 100개")).toBeInTheDocument();
    expect(screen.getByText("선택 50/50")).toBeInTheDocument();

    await screen.findByText("BTC / KRW");
    await user.click(screen.getAllByRole("checkbox")[0]);

    expect(screen.getByText("선택 49/50")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "저장" })).toBeEnabled();
    expect(screen.getByText("대상 변경 1건")).toBeInTheDocument();
    expect(screen.getByText("100,000,000,000 ￦")).toBeInTheDocument();
    expect(screen.getByText("24시간 거래대금")).toBeInTheDocument();
    expect(screen.queryByText("품질")).not.toBeInTheDocument();
    expect(screen.getByText("수집 시작일")).toBeInTheDocument();
    expect(screen.getByText("수집 최종일")).toBeInTheDocument();
    expect(screen.getAllByText("2026.01.01 00:00:00 KST")[0]).toBeInTheDocument();
    expect(screen.getAllByText("2026.06.19 09:00:00 KST")[0]).toBeInTheDocument();
    expect(screen.getAllByText("실시간")[0]).toBeInTheDocument();
    expect(screen.queryByText("수집", { selector: ".target-row span" })).not.toBeInTheDocument();

    await user.type(screen.getByPlaceholderText("코인명 또는 심볼 검색"), "GM050");
    expect(screen.getByText("GM050 / KRW")).toBeInTheDocument();
    expect(screen.queryByText("BTC / KRW")).not.toBeInTheDocument();
    await user.clear(screen.getByPlaceholderText("코인명 또는 심볼 검색"));
    expect(screen.queryByRole("option", { name: "품질순" })).not.toBeInTheDocument();
  });

  it("수집 대상 화면에서 선택 코인으로 백필 작업을 바로 시작한다", async () => {
    const user = userEvent.setup();
    render(<App />);

    await screen.findByRole("heading", { name: "업비트 수집 운영 상태" });
    await user.click(screen.getByRole("button", { name: "Backfill 관리" }));
    await user.click(screen.getByRole("button", { name: "백필 계획 생성" }));

    expect(await screen.findByRole("dialog", { name: "백필 계획 생성" })).toBeInTheDocument();
    expect(screen.getByText("선택 코인 50개")).toBeInTheDocument();
    await user.clear(screen.getByLabelText("수집 범위 시작"));
    await user.type(screen.getByLabelText("수집 범위 시작"), "2026-01-01T00:00");
    await user.clear(screen.getByLabelText("수집 범위 종료"));
    await user.type(screen.getByLabelText("수집 범위 종료"), "2026-01-03T00:00");
    await user.click(screen.getByRole("button", { name: "백필 시작" }));

    const fetchMock = vi.mocked(globalThis.fetch);
    const jobRequest = fetchMock.mock.calls.find(
      ([input, init]) => String(input).endsWith("/v1/backfill/jobs") && init?.method === "POST"
    );
    const jobBody = JSON.parse(String((jobRequest?.[1] as RequestInit).body));
    expect(jobBody).toMatchObject({
      dataType: "source_candle",
      targetStartAt: "2026-01-01T00:00:00+09:00",
      targetEndAt: "2026-01-03T00:00:00+09:00",
      instrumentIds: expect.arrayContaining([1, 2])
    });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "백필 계획 생성" })).toBeNull());
    expect(screen.queryByRole("button", { name: "백필 계획 승인" })).not.toBeInTheDocument();
  });

  it("수집 대상 화면에서 백필 작업 목록과 실행 상태를 표시한다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        createTestOperationsFetch({
          backfillJobs: [
            {
              id: 77,
              status: "running",
              dataType: "source_candle",
              progressPercent: "42.5",
              totalTargetCount: 3,
              completedTargetCount: 1,
              runningTargetIndex: 2,
              currentTarget: {
                id: 2,
                exchange: "UPBIT",
                marketCode: "KRW-ETH",
                quoteCurrency: "KRW",
                baseAsset: "ETH",
                displayName: "이더리움"
              },
              currentTargetBackfillRowCount: 120,
              processedMissingRangeCount: 3,
              estimatedMissingRangeCount: 9,
              estimatedRequestCount: 42,
              targetStartAt: "2026-01-01T00:00:00+09:00",
              targetEndAt: "2026-02-01T00:00:00+09:00",
              targets: [
                {
                  id: 1,
                  exchange: "UPBIT",
                  marketCode: "KRW-BTC",
                  quoteCurrency: "KRW",
                  baseAsset: "BTC",
                  displayName: "비트코인"
                },
                {
                  id: 2,
                  exchange: "UPBIT",
                  marketCode: "KRW-ETH",
                  quoteCurrency: "KRW",
                  baseAsset: "ETH",
                  displayName: "이더리움"
                }
              ],
              createdAt: "2026-06-21T09:00:00+09:00"
            },
            {
              id: 76,
              status: "paused",
              dataType: "source_candle",
              progressPercent: "10",
              estimatedRequestCount: 3,
              totalTargetCount: 50,
              completedTargetCount: 5,
              runningTargetIndex: null,
              currentTarget: null,
              currentTargetBackfillRowCount: 0,
              processedMissingRangeCount: 0,
              estimatedMissingRangeCount: 0,
              targetStartAt: "2026-01-01T00:00:00+09:00",
              targetEndAt: "2026-01-03T00:00:00+09:00",
              targets: createTestInstruments(50),
              createdAt: "2026-06-20T18:30:00+09:00"
            },
            {
              id: 75,
              status: "succeeded",
              dataType: "source_candle",
              progressPercent: "100",
              estimatedRequestCount: 1,
              totalTargetCount: 1,
              completedTargetCount: 1,
              runningTargetIndex: null,
              currentTarget: null,
              currentTargetBackfillRowCount: 0,
              processedMissingRangeCount: 0,
              estimatedMissingRangeCount: 0,
              targetStartAt: "2026-01-01T00:00:00+09:00",
              targetEndAt: "2026-01-03T00:00:00+09:00",
              targets: createTestInstruments(1),
              createdAt: "2026-06-20T12:00:00+09:00"
            },
            {
              id: 74,
              status: "failed",
              dataType: "source_candle",
              progressPercent: "18.5",
              estimatedRequestCount: 12,
              totalTargetCount: 2,
              completedTargetCount: 0,
              runningTargetIndex: null,
              currentTarget: null,
              currentTargetBackfillRowCount: 0,
              processedMissingRangeCount: 1,
              estimatedMissingRangeCount: 4,
              targetStartAt: "2026-01-01T00:00:00+09:00",
              targetEndAt: "2026-01-03T00:00:00+09:00",
              targets: createTestInstruments(2),
              createdAt: "2026-06-20T11:00:00+09:00"
            }
          ]
        })
      )
    );
    const user = userEvent.setup();
    render(<App />);

    await screen.findByRole("heading", { name: "업비트 수집 운영 상태" });
    await user.click(screen.getByRole("button", { name: "Backfill 관리" }));

    expect(await screen.findByRole("heading", { name: "백필 작업" })).toBeInTheDocument();
    const panel = screen.getByLabelText("백필 작업 목록");
    const runningCard = within(panel).getByText("작업 77").closest("article");
    expect(runningCard).not.toBeNull();
    expect(within(runningCard as HTMLElement).getByText("실행 중")).toBeInTheDocument();
    expect(within(runningCard as HTMLElement).getByText("42.5%")).toBeInTheDocument();
    expect(within(runningCard as HTMLElement).getByText("대상 2/3")).toBeInTheDocument();
    expect(within(runningCard as HTMLElement).getByText("완료 1개")).toBeInTheDocument();
    expect(within(runningCard as HTMLElement).getByText("현재 ETH")).toBeInTheDocument();
    expect(within(runningCard as HTMLElement).getByText("백필 row 120")).toBeInTheDocument();
    expect(within(runningCard as HTMLElement).getByText("결측 구간 처리 3/9")).toBeInTheDocument();
    expect(within(runningCard as HTMLElement).getByText("예상 요청 42")).toBeInTheDocument();
    expect(within(panel).getAllByText("1분 캔들(Source Candle)")).toHaveLength(4);
    expect(within(runningCard as HTMLElement).getByText("BTC, ETH")).toBeInTheDocument();
    expect(within(panel).getByText("2026.01.01 00:00:00 KST ~ 2026.02.01 00:00:00 KST")).toBeInTheDocument();
    expect(within(panel).getByText("2026.06.21 09:00:00 KST")).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "작업 77 멈춤" })).toBeEnabled();
    expect(within(panel).getByRole("button", { name: "작업 77 중지" })).toBeEnabled();
    expect(within(panel).getByRole("button", { name: "작업 77 삭제" })).toBeDisabled();
    expect(within(panel).getByText("작업 76")).toBeInTheDocument();
    expect(within(panel).getByText("일시정지")).toBeInTheDocument();
    const targetSummary = within(panel).getByText("BTC, ETH, GM003, GM004 외 46개");
    expect(targetSummary).toHaveAttribute(
      "title",
      createTestInstruments(50).map((target) => target.baseAsset).join(", ")
    );
    expect(within(panel).getByLabelText("작업 76 대상 전체 보기")).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "작업 76 재개" })).toBeEnabled();
    expect(within(panel).getByRole("button", { name: "작업 74 재개" })).toBeEnabled();
    expect(within(panel).getByRole("button", { name: "작업 75 삭제" })).toBeEnabled();

    await user.click(within(panel).getByRole("button", { name: "작업 77 멈춤" }));
    await user.click(within(panel).getByRole("button", { name: "작업 77 중지" }));
    await user.click(within(panel).getByRole("button", { name: "작업 76 재개" }));
    await user.click(within(panel).getByRole("button", { name: "작업 74 재개" }));
    await user.click(within(panel).getByRole("button", { name: "작업 75 삭제" }));

    const requests = vi.mocked(globalThis.fetch).mock.calls.map(([input, init]) => ({
      url: String(input),
      method: init?.method ?? "GET"
    }));
    expect(requests).toContainEqual({
      url: "/api/v1/backfill/jobs/77/pause",
      method: "POST"
    });
    expect(requests).toContainEqual({
      url: "/api/v1/backfill/jobs/77/stop",
      method: "POST"
    });
    expect(requests).toContainEqual({
      url: "/api/v1/backfill/jobs/76/resume",
      method: "POST"
    });
    expect(requests).toContainEqual({
      url: "/api/v1/backfill/jobs/74/resume",
      method: "POST"
    });
    expect(requests).toContainEqual({
      url: "/api/v1/backfill/jobs/75",
      method: "DELETE"
    });
  });

  it("관심종목은 코인 목록의 가격, 기준일시, 캔들 커버리지를 표시한다", async () => {
    const user = userEvent.setup();
    render(<App />);

    await screen.findByRole("heading", { name: "업비트 수집 운영 상태" });
    expect(screen.queryByRole("button", { name: "시장 리스트" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "관심종목" }));
    expect((await screen.findAllByRole("heading", { name: "관심종목" }))[0]).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^코인$/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^주식$/ })).not.toBeInTheDocument();
    expect(await screen.findByText("거래 상품")).toBeInTheDocument();
    expect(screen.getByText("관심 추가")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /등락률 .* KST 기준 정렬/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /24시간 거래대금 정렬/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /기준일시 정렬/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /캔들 커버리지 정렬/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /1분 캔들 수 정렬/ })).toBeInTheDocument();
    expect(screen.queryByText("최신성")).not.toBeInTheDocument();
    expect(screen.queryByText("저장 행")).not.toBeInTheDocument();
    expect(screen.queryByText("품질")).not.toBeInTheDocument();
    expect(screen.getByText("BTC / KRW")).toBeInTheDocument();
    expect(screen.getByText("1,000,000 ￦")).toBeInTheDocument();
    expect(screen.queryByText("1,000,000.9876")).not.toBeInTheDocument();
    expect(screen.getByText("100,000,000,000 ￦")).toBeInTheDocument();
    expect(screen.queryByText("100,000,000,000.9876")).not.toBeInTheDocument();
    expect(screen.getAllByText("2026.01.01")[0]).toBeInTheDocument();
    expect(screen.getAllByText("2026.06.19")[0]).toBeInTheDocument();
    expect(screen.getAllByText("2026.06.19 09:00:00 KST")[0]).toBeInTheDocument();
    expect(screen.getByText("1,000")).toBeInTheDocument();
    expect(screen.getByText("GM050 / KRW")).toBeInTheDocument();
    const noCandleRow = screen.getByText("GM076 / KRW").closest(".table-row");
    expect(noCandleRow).not.toBeNull();
    expect(within(noCandleRow as HTMLElement).getByText("2026.01.01")).toBeInTheDocument();
    expect(within(noCandleRow as HTMLElement).getByText("0")).toBeInTheDocument();
  });

  it("관심종목에서 코인 관심 순서와 관심 추가 상태를 조정한다", async () => {
    const user = userEvent.setup();
    render(<App />);

    await screen.findByRole("heading", { name: "업비트 수집 운영 상태" });
    await user.click(screen.getByRole("button", { name: "관심종목" }));
    expect((await screen.findAllByRole("heading", { name: "관심종목" }))[0]).toBeInTheDocument();
    expect(screen.getByText("관심추가 항목")).toBeInTheDocument();
    expect(screen.getByText("후보 종목")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /24시간 거래대금 정렬/ })).toHaveAttribute(
      "aria-sort",
      "descending"
    );
    expect(screen.getByRole("button", { name: /등락률 .* KST 기준 정렬/ })).toBeInTheDocument();

    await user.type(screen.getByPlaceholderText("종목명 또는 심볼 검색"), "이더");
    expect(screen.getByText("ETH / KRW")).toBeInTheDocument();
    expect(screen.queryByText("BTC / KRW")).not.toBeInTheDocument();
    await user.clear(screen.getByPlaceholderText("종목명 또는 심볼 검색"));
    await user.click(screen.getByRole("button", { name: "거래 상품 정렬" }));
    expect(screen.getByRole("button", { name: "거래 상품 정렬" })).toHaveAttribute(
      "aria-sort",
      "ascending"
    );

    await user.click(screen.getByRole("button", { name: "ETH 관심 순서 위로" }));
    const reorderedRequest = [...vi.mocked(globalThis.fetch).mock.calls]
      .reverse()
      .find(
        ([input, init]) =>
          String(input).endsWith("/v1/collection-targets") && init?.method === "PUT"
      );
    expect(reorderedRequest).toBeDefined();
    const reorderedBody = JSON.parse(String((reorderedRequest?.[1] as RequestInit).body));
    expect(reorderedBody.instrumentIds.slice(0, 2)).toEqual([2, 1]);
    await waitFor(() => {
      const favoriteRows = screen.getAllByRole("button", { name: /관심 제거$/ });
      expect(favoriteRows[0]).toHaveAccessibleName("ETH 관심 제거");
      expect(favoriteRows[1]).toHaveAccessibleName("BTC 관심 제거");
    });
    await user.click(screen.getByRole("button", { name: "관심 코인 50개 보기" }));
    const favoriteDialog = await screen.findByRole("dialog", { name: "관심 코인 목록" });
    expect(within(favoriteDialog).getAllByText(/\/ KRW/)[0]).toHaveTextContent("ETH / KRW");
    await user.click(within(favoriteDialog).getByRole("button", { name: "닫기" }));

    await user.click(screen.getByRole("button", { name: "BTC 관심 제거" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "BTC 관심 추가" })).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "관심 코인 49개 보기" })).toBeInTheDocument();
    await waitFor(() => {
      const targetRequest = [...vi.mocked(globalThis.fetch).mock.calls]
        .reverse()
        .find(
          ([input, init]) =>
            String(input).endsWith("/v1/collection-targets") && init?.method === "PUT"
        );
      expect(targetRequest).toBeDefined();
      const body = JSON.parse(String((targetRequest?.[1] as RequestInit).body));
      expect(body.instrumentIds).not.toContain(1);
      expect(body.reason).toBe("관심종목 화면에서 관심목록 변경");
    });
    await user.click(screen.getByRole("button", { name: "GM051 관심 추가" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "GM051 관심 제거" })).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "관심 코인 50개 보기" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Backfill 관리" }));
    await user.click(screen.getByRole("button", { name: /^저장$/ }));
    await waitFor(() => {
      const targetRequest = [...vi.mocked(globalThis.fetch).mock.calls]
        .reverse()
        .find(
          ([input, init]) =>
            String(input).endsWith("/v1/collection-targets") && init?.method === "PUT"
        );
      expect(targetRequest).toBeDefined();
      const body = JSON.parse(String((targetRequest?.[1] as RequestInit).body));
      expect(body.instrumentIds[0]).toBe(2);
      expect(body.instrumentIds).not.toContain(1);
      expect(body.reason).toBe("운영 화면에서 수집 대상 변경");
    });
  }, 10_000);

  it("관심종목에서 코인 상세 열기를 제공한다", async () => {
    const user = userEvent.setup();
    render(<App />);

    await screen.findByRole("heading", { name: "업비트 수집 운영 상태" });
    await user.click(screen.getByRole("button", { name: "관심종목" }));
    expect((await screen.findAllByRole("heading", { name: "관심종목" }))[0]).toBeInTheDocument();

    expect(screen.queryByRole("button", { name: /^주식$/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /GM003 \/ KRW/ }));

    expect(await screen.findByRole("dialog", { name: "코인 상세" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "GM003 / KRW" })).toBeInTheDocument();
    expect(screen.getByText("2026년 1월 1분봉")).toBeInTheDocument();
    expect(screen.getByLabelText("TradingView 캔들 차트")).toBeInTheDocument();
    expect(screen.getByText("현재가 게이지")).toBeInTheDocument();
    expect(screen.getByText("24H 변동금액")).toBeInTheDocument();
    expect(screen.getByText("24H 거래량")).toBeInTheDocument();
    expect(screen.queryByText("중복 행")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "수집 품질 이력" })).toBeInTheDocument();
    expect(screen.getAllByText(/캔들|현재가|호가/)[0]).toBeInTheDocument();
    expect(document.querySelector(".modal-backdrop")).toBeInTheDocument();
  });

  it("관심종목 SSE 이벤트를 받으면 가격 정보를 갱신한다", async () => {
    const user = userEvent.setup();
    const eventSources: Array<{
      url: string;
      listeners: Map<string, (event: MessageEvent<string>) => void>;
      close: () => void;
    }> = [];
    class FakeEventSource {
      url: string;
      listeners = new Map<string, (event: MessageEvent<string>) => void>();

      constructor(url: string) {
        this.url = url;
        eventSources.push(this);
      }

      addEventListener(type: string, handler: EventListener) {
        this.listeners.set(type, handler as (event: MessageEvent<string>) => void);
      }

      close() {
        return undefined;
      }
    }
    vi.stubGlobal("EventSource", FakeEventSource);
    render(<App />);

    await screen.findByRole("heading", { name: "업비트 수집 운영 상태" });
    await user.click(screen.getByRole("button", { name: "관심종목" }));
    expect(await screen.findByText("1,000,000 ￦")).toBeInTheDocument();

    const marketStream = eventSources.find((source) => source.url.endsWith("/v1/market-list/stream"));
    const marketListHandler = marketStream?.listeners.get("marketList");
    if (!marketListHandler) {
      throw new Error("관심종목 SSE 구독이 등록되지 않았습니다.");
    }
    const streamedRows = createTestMarketRows();
    streamedRows[0] = {
      ...streamedRows[0],
      tradePrice: "1234567.89",
      accTradePrice24h: "2222222222.77",
      tickerCollectedAt: "2026-06-20T09:30:00+09:00"
    };
    marketListHandler(
      new MessageEvent("marketList", {
        data: JSON.stringify({ rows: streamedRows })
      })
    );

    await waitFor(() => expect(screen.getByText("1,234,567 ￦")).toBeInTheDocument());
    expect(screen.getByText("2,222,222,222 ￦")).toBeInTheDocument();
    expect(screen.queryByText("1,234,567.89")).not.toBeInTheDocument();
  });

  it("확장성 점검 메뉴를 노출하지 않는다", async () => {
    render(<App />);

    await screen.findByRole("heading", { name: "업비트 수집 운영 상태" });
    expect(screen.queryByRole("button", { name: "확장성 점검" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "확장성 점검" })).not.toBeInTheDocument();
  });
});
