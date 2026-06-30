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
    expect(screen.getAllByText("최신수집중")[0]).toBeInTheDocument();
    expect(screen.getByText("운영 헬스")).toBeInTheDocument();
    expect(screen.getAllByText("KST")[0]).toBeInTheDocument();
    expect(screen.queryByText("UTC")).not.toBeInTheDocument();
    expect(container.querySelector(".app-shell")).toHaveAttribute("data-theme", "dark");
  });

  it("운영 상태 행을 펼쳐 코인별 수집 계획과 구간형 진행 상태를 표시한다", async () => {
    const user = userEvent.setup();
    render(<App />);

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "업비트 수집 운영 상태" })).toBeInTheDocument()
    );

    await user.click(screen.getByRole("button", { name: /BTC \/ KRW/ }));

    expect(await screen.findByText("수집 계획")).toBeInTheDocument();
    expect(screen.getAllByText("2026-01-01 00:00 KST ~ 현재(지속)")[0]).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "수정" })).toBeInTheDocument();
    expect(screen.getByText("캔들")).toBeInTheDocument();
    expect(screen.getByText("현재가")).toBeInTheDocument();
    expect(screen.getAllByText("호가 요약")[0]).toBeInTheDocument();
    expect(document.querySelector(".coverage-segment.missing")).toBeInTheDocument();
  });

  it("관심종목 설정은 선택 항목 분리, 실시간 검색, 헤더 정렬을 지원한다", async () => {
    const user = userEvent.setup();
    render(<App />);

    await screen.findByRole("heading", { name: "업비트 수집 운영 상태" });
    await user.click(screen.getByRole("button", { name: "수집 대상 설정" }));
    expect(await screen.findByText("관심추가 항목")).toBeInTheDocument();
    expect(await screen.findByText("후보 유니버스 상위 100개")).toBeInTheDocument();
    expect(screen.getByText("선택 50/50")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "24시간 거래대금 정렬" })).toHaveAttribute(
      "aria-sort",
      "descending"
    );

    await screen.findByText("BTC / KRW");
    await user.type(screen.getByPlaceholderText("코인명 또는 심볼 검색"), "이더");

    expect(screen.getByText("ETH / KRW")).toBeInTheDocument();
    expect(screen.queryByText("BTC / KRW")).not.toBeInTheDocument();

    await user.clear(screen.getByPlaceholderText("코인명 또는 심볼 검색"));
    await user.click(screen.getByRole("button", { name: "종목 정렬" }));

    expect(screen.getByRole("button", { name: "종목 정렬" })).toHaveAttribute(
      "aria-sort",
      "ascending"
    );

    await user.click(screen.getAllByRole("checkbox", { name: "관심추가" })[0]);

    expect(screen.getByText("선택 49/50")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "저장" })).toBeEnabled();
  });

  it("시장 리스트에서 코인을 누르면 dimmed 레이어 팝업으로 코인 상세를 표시한다", async () => {
    const user = userEvent.setup();
    render(<App />);

    await screen.findByRole("heading", { name: "업비트 수집 운영 상태" });
    await user.click(screen.getByRole("button", { name: "시장 리스트" }));
    expect(await screen.findByText("거래 상품")).toBeInTheDocument();
    expect(screen.getByText("등락률")).toBeInTheDocument();
    expect(screen.getByText("06.18 09:00 KST 기준")).toBeInTheDocument();
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
