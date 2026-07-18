import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { BotWorkshop } from "./BotWorkshop";

afterEach(() => {
  cleanup();
});

describe("Bot Workshop", () => {
  it("포트폴리오 배정부터 paper/shadow 운영과 대사 증적까지 읽기 전용으로 표시한다", () => {
    render(<BotWorkshop />);

    expect(screen.getByRole("heading", { name: "Bot Workshop" })).toBeInTheDocument();
    expect(screen.getByText("Portfolio allocation → paper 운영 준비")).toBeInTheDocument();
    expect(screen.getByText("draft · 설계 중")).toBeInTheDocument();
    expect(screen.getByText("backtest_ready · 백테스트 준비")).toBeInTheDocument();
    expect(screen.getByText("paper · paper rehearsal")).toBeInTheDocument();
    expect(screen.getByText("shadow · shadow rehearsal")).toBeInTheDocument();
    expect(screen.getByText("live_ready · 안전 잠금")).toBeInTheDocument();
    expect(screen.getByText("live · 안전 잠금")).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "live 안전 잠금" })).toHaveTextContent(
      "live-ready · live 잠금"
    );
    const pipeline = screen.getByRole("region", { name: "주문 파이프라인" });
    expect(pipeline).toHaveTextContent(
      /order intent.*risk evaluation.*paper execution job.*reconciliation.*position projection/
    );
    expect(screen.getByRole("region", { name: "킬스위치와 승인 checklist" }))
      .toHaveTextContent("global kill switch");
    expect(screen.getByRole("region", { name: "대사 증적" }))
      .toHaveTextContent("reconciliation_mismatch");
    expect(screen.getByRole("region", { name: "대사 증적" }))
      .toHaveTextContent("outcome_unknown");
  });

  it("실제 주문 제출 또는 live 활성화 동작을 제공하지 않는다", () => {
    render(<BotWorkshop />);

    expect(screen.queryByRole("button", { name: /주문.*제출|live.*활성화/i })).not.toBeInTheDocument();
    expect(screen.queryByText("private WebSocket")).not.toBeInTheDocument();
    expect(screen.queryByText("주문 테스트 API")).not.toBeInTheDocument();
    expect(screen.queryByText(/주식|stock/i)).not.toBeInTheDocument();
  });
});
