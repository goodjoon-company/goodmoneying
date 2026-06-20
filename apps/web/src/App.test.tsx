import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

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
    expect(await screen.findByText("활성 대상")).toBeInTheDocument();
    expect(screen.getAllByText("BTC / KRW")[0]).toBeInTheDocument();
    expect(screen.getByText("코인별 수집 상태")).toBeInTheDocument();
    expect(screen.getByText("운영 헬스")).toBeInTheDocument();
    expect(screen.getByText(/마지막 갱신/)).toBeInTheDocument();
    expect(screen.getByText("표시 KST")).toBeInTheDocument();
    expect(screen.getByText("저장 UTC")).toBeInTheDocument();
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

  it("수집 대상 설정은 최대 50개 후보 선택을 저장한다", async () => {
    const user = userEvent.setup();
    render(<App />);

    await screen.findByRole("heading", { name: "업비트 수집 운영 상태" });
    await user.click(screen.getByRole("button", { name: "수집 대상 설정" }));
    expect(await screen.findByText("후보 유니버스 상위 100개")).toBeInTheDocument();
    expect(screen.getByText("선택 50/50")).toBeInTheDocument();

    await screen.findByText("BTC / KRW");
    await user.click(screen.getAllByRole("checkbox")[0]);

    expect(screen.getByText("선택 49/50")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "저장" })).toBeEnabled();
    expect(screen.getByText("대상 변경 50건")).toBeInTheDocument();
    expect(screen.getByText(/^₩100,000,000,000/)).toBeInTheDocument();
    expect(screen.getAllByTitle(/품질/)[0]).toHaveTextContent(/주의|정상/);
    expect(screen.getAllByText("2026-01-01 00:00 KST ~ NOW")[0]).toBeInTheDocument();

    await user.type(screen.getByPlaceholderText("코인명 또는 심볼 검색"), "GM050");
    expect(screen.getByText("GM050 / KRW")).toBeInTheDocument();
    expect(screen.queryByText("BTC / KRW")).not.toBeInTheDocument();
    await user.clear(screen.getByPlaceholderText("코인명 또는 심볼 검색"));
    await user.selectOptions(screen.getByRole("combobox", { name: "후보 정렬" }), "quality");
    expect(screen.getByRole("combobox", { name: "후보 정렬" })).toHaveValue("quality");
  });

  it("수집 대상 화면에서 선택 코인으로 백필 계획을 만들고 승인한다", async () => {
    const user = userEvent.setup();
    const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/v1/backfill/plans")) {
        return Response.json({
          planId: "plan-1",
          dataType: "source_candle",
          estimatedRequestCount: 12,
          estimatedRowCount: 2880,
          estimatedStorageBytes: 737280,
          targets: [1, 2]
        });
      }
      if (url.endsWith("/v1/backfill/jobs")) {
        return Response.json(
          {
            id: 77,
            status: "pending",
            dataType: "source_candle",
            progressPercent: "0",
            createdAt: "2026-06-19T00:00:00.000Z"
          },
          { status: 201 }
        );
      }
      return new Response(`unexpected ${url}`, { status: 500 });
    });
    vi.stubGlobal("fetch", fetch);
    render(<App />);

    await screen.findByRole("heading", { name: "업비트 수집 운영 상태" });
    await user.click(screen.getByRole("button", { name: "수집 대상 설정" }));
    await user.click(screen.getByRole("button", { name: "백필 계획 생성" }));

    expect(await screen.findByRole("dialog", { name: "백필 계획 생성" })).toBeInTheDocument();
    expect(screen.getByText("선택 코인 50개")).toBeInTheDocument();
    await user.clear(screen.getByLabelText("수집 범위 시작"));
    await user.type(screen.getByLabelText("수집 범위 시작"), "2026-01-01T00:00");
    await user.clear(screen.getByLabelText("수집 범위 종료"));
    await user.type(screen.getByLabelText("수집 범위 종료"), "2026-01-03T00:00");
    await user.click(screen.getByRole("button", { name: "확인" }));

    expect(await screen.findByText("계획 plan-1")).toBeInTheDocument();
    expect(screen.getByText("대상 2개")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "백필 계획 승인" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "백필 계획 승인" }));

    const planRequest = fetch.mock.calls.find(([input]) =>
      String(input).endsWith("/v1/backfill/plans")
    );
    const planBody = JSON.parse(String((planRequest?.[1] as RequestInit).body));
    expect(planBody).toMatchObject({
      dataType: "source_candle",
      targetStartAt: "2026-01-01T00:00:00.000Z",
      targetEndAt: "2026-01-03T00:00:00.000Z",
      instrumentIds: expect.arrayContaining([1, 2])
    });
    expect(await screen.findByText("승인된 작업 77")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "백필 계획 승인" })).toBeDisabled();
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
