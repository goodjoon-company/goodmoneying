import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import {
  createTestDashboardSummary,
  createTestInstruments,
  createTestOperationsFetch
} from "./testOperationsApi";

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(createTestOperationsFetch()));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("데이터 수집관리 화면", () => {
  it("좌측 내비게이션과 운영 상태 대시보드를 첫 화면으로 표시한다", async () => {
    const { container } = render(<App />);

    expect(await screen.findByText("goodmoneying")).toBeInTheDocument();
    expect(await screen.findByText("데이터 수집관리")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "운영 상태" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "코인 상세" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "CSV 내보내기" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "운영 변경 저장" })).not.toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "업비트 수집 운영 상태" })).toBeInTheDocument();
    expect(await screen.findByText("worker 현황")).toBeInTheDocument();
    expect(await screen.findByText("Realtime worker")).toBeInTheDocument();
    expect(await screen.findByText("Backfill worker")).toBeInTheDocument();
    expect(screen.getAllByText("BTC / KRW")[0]).toBeInTheDocument();
    expect(screen.getByText("코인별 수집 상태")).toBeInTheDocument();
    expect(screen.getByText("운영 헬스")).toBeInTheDocument();
    expect(screen.getByText(/마지막 갱신/)).toBeInTheDocument();
    expect(screen.getByText("표시 KST")).toBeInTheDocument();
    expect(screen.getByText("저장 KST")).toBeInTheDocument();
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
    expect(await screen.findByText("후보 유니버스 상위 100개")).toBeInTheDocument();
    expect(screen.getByText("선택 50/50")).toBeInTheDocument();

    await screen.findByText("BTC / KRW");
    await user.click(screen.getAllByRole("checkbox")[0]);

    expect(screen.getByText("선택 49/50")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "저장" })).toBeEnabled();
    expect(screen.getByText("대상 변경 1건")).toBeInTheDocument();
    expect(screen.getByText(/^₩100,000,000,000/)).toBeInTheDocument();
    expect(screen.getByText("24시간 거래대금")).toBeInTheDocument();
    expect(screen.queryByText("품질")).not.toBeInTheDocument();
    expect(screen.getByText("수집 시작일")).toBeInTheDocument();
    expect(screen.getByText("수집 최종일")).toBeInTheDocument();
    expect(screen.getAllByText("2026-01-01 00:00 KST")[0]).toBeInTheDocument();
    expect(screen.getAllByText("2026-06-19 09:00 KST")[0]).toBeInTheDocument();
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
    expect(within(panel).getAllByText("1분 캔들(Source Candle)")).toHaveLength(3);
    expect(within(panel).getByText("BTC, ETH")).toBeInTheDocument();
    expect(within(panel).getByText("2026년 01월 01일 00:00 ~ 2026년 02월 01일 00:00")).toBeInTheDocument();
    expect(within(panel).getByText(/06. 21./)).toBeInTheDocument();
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
    expect(within(panel).getByRole("button", { name: "작업 75 삭제" })).toBeEnabled();

    await user.click(within(panel).getByRole("button", { name: "작업 77 멈춤" }));
    await user.click(within(panel).getByRole("button", { name: "작업 77 중지" }));
    await user.click(within(panel).getByRole("button", { name: "작업 76 재개" }));
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
      url: "/api/v1/backfill/jobs/75",
      method: "DELETE"
    });
  });

  it("시장 리스트에서 코인을 누르면 dimmed 레이어 팝업으로 코인 상세를 표시한다", async () => {
    const user = userEvent.setup();
    render(<App />);

    await screen.findByRole("heading", { name: "업비트 수집 운영 상태" });
    await user.click(screen.getByRole("button", { name: "시장 리스트" }));
    expect(await screen.findByText("거래 상품")).toBeInTheDocument();
    expect(screen.getByText("등락률")).toBeInTheDocument();
    expect(screen.getByText("24시간 거래대금")).toBeInTheDocument();
    expect(screen.getByText("BTC / KRW")).toBeInTheDocument();
    expect(screen.getByText("GM050 / KRW")).toBeInTheDocument();
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

  it("확장성 점검은 M3.5 준비 상태만 표시하고 실제 모니터링 수치를 만들지 않는다", async () => {
    const user = userEvent.setup();
    render(<App />);

    await screen.findByRole("heading", { name: "업비트 수집 운영 상태" });
    await user.click(screen.getByRole("button", { name: "확장성 점검" }));

    expect((await screen.findAllByRole("heading", { name: "확장성 점검" }))[0]).toBeInTheDocument();
    expect(screen.getByText("수평 확장")).toBeInTheDocument();
    expect(screen.getByText("메시지 큐")).toBeInTheDocument();
    expect(screen.getAllByText(/M3.5/)[0]).toBeInTheDocument();
    expect(screen.queryByText(/CPU|메모리|TPS|QPS/)).not.toBeInTheDocument();
  });
});
