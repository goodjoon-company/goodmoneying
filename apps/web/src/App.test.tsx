import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { App } from "./App";

afterEach(() => {
  cleanup();
});

describe("데이터 수집관리 화면", () => {
  it("좌측 내비게이션과 운영 상태 대시보드를 첫 화면으로 표시한다", async () => {
    const { container } = render(<App />);

    expect(await screen.findByText("goodmoneying")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "데이터 수집관리" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "종목 발굴" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "매매 전략" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "봇 관리" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "시스템 관리" })).toBeDisabled();
    expect(await screen.findByRole("heading", { name: "업비트 수집 운영 상태" })).toBeInTheDocument();
    expect(await screen.findByText("활성 수집 대상")).toBeInTheDocument();
    expect(screen.getByText("BTC / KRW")).toBeInTheDocument();
    expect(screen.getAllByText(/마지막 호가/)[0]).toBeInTheDocument();
    expect(screen.getByText("운영 헬스")).toBeInTheDocument();
    expect(screen.getByText(/마지막 갱신/)).toBeInTheDocument();
    expect(screen.getAllByText("KST")[0]).toBeInTheDocument();
    expect(screen.queryByText("UTC")).not.toBeInTheDocument();
    expect(container.querySelector(".app-shell")).toHaveAttribute("data-theme", "dark");
  });

  it("운영 상태는 코인별 실시간 수집과 Backfill 상태를 동적인 숫자로 표시한다", async () => {
    const user = userEvent.setup();
    render(<App />);

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "업비트 수집 운영 상태" })).toBeInTheDocument()
    );

    expect(screen.queryByText("구간형 수집 진행 상태")).not.toBeInTheDocument();
    expect(screen.getByText("실시간 수집")).toBeInTheDocument();
    expect(screen.getByText("Backfill")).toBeInTheDocument();
    expect(screen.getByText("가격 분봉")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /BTC \/ KRW/ }));

    expect(await screen.findByText("수집 계획")).toBeInTheDocument();
    expect(screen.getAllByText("2026-01-01 00:00 KST ~ NOW")[0]).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "수정" })).toBeInTheDocument();
    expect(screen.getByText("캔들")).toBeInTheDocument();
    expect(screen.getByText("현재가")).toBeInTheDocument();
    expect(screen.getAllByText("호가 요약")[0]).toBeInTheDocument();
    expect(document.querySelector(".candle-count-meter")).toBeInTheDocument();
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
    expect(screen.getByText(/^₩100,000,000,000/)).toBeInTheDocument();
    expect(screen.getAllByTitle(/품질/)[0]).toHaveTextContent(/주의|정상/);
    expect(screen.getAllByText("2024-01-01 00:00 KST ~ NOW")[0]).toBeInTheDocument();
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
    expect(document.querySelector(".modal-backdrop")).toBeInTheDocument();
  });
});
