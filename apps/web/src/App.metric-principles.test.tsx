import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  cleanup();
  vi.doUnmock("./api");
  vi.resetModules();
});

describe("운영 콘솔 관측 지표 원칙", () => {
  it("미노출 지표 원칙을 별도 패널로 렌더링하지 않고 실제 대체 지표만 표시한다", async () => {
    vi.doMock("./api", async (importOriginal) => {
      const actual = await importOriginal<typeof import("./api")>();
      const snapshot = actual.demoSnapshot();
      return {
        ...actual,
        demoSnapshot: () => ({
          ...snapshot,
          dashboard: {
            ...snapshot.dashboard,
            metricPrinciples: [
              {
                metricKey: "rateLimitRemainingPercent",
                label: "업비트 Rate Limit 여유율",
                displayStatus: "excluded",
                evidenceStatus: "missing_persistence",
                reason: "실제 Upbit 헤더 영속화가 없어 운영 콘솔에서 제외한다."
              },
              {
                metricKey: "duplicateRows24h",
                label: "중복 저장 시도",
                displayStatus: "excluded",
                evidenceStatus: "missing_measurement",
                reason: "업서트 충돌 또는 중복 시도 측정값이 없어 운영 콘솔에서 제외한다."
              }
            ]
          }
        })
      };
    });
    const { App } = await import("./App");

    render(<App />);

    expect(await screen.findByText("최근 1분 수집 건수")).toBeInTheDocument();
    expect(screen.getByText("실시간 / 백필 row")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "관측 지표 원칙" })).not.toBeInTheDocument();
    expect(screen.queryByText("업비트 Rate Limit 여유율")).not.toBeInTheDocument();
    expect(screen.queryByText("중복 저장 시도")).not.toBeInTheDocument();
    expect(screen.queryByText("Rate limit 여유율")).not.toBeInTheDocument();
    expect(screen.queryByText("중복 행")).not.toBeInTheDocument();
  });
});
