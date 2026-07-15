import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SystemManagement } from "./SystemManagement";

vi.mock("../useSystemManagement", () => ({
  useSystemManagement: () => ({
    connectionStatus: "live",
    snapshot: {
      refreshedAt: "2026-07-16T09:00:00+09:00",
      realtime: { status: "running", statusLabel: "동작 중", items: [] },
      backfill: { status: "running", statusLabel: "동작 중", items: [] },
      aggregationWorker: {
        status: "running",
        statusLabel: "동작 중",
        statusDetail: "최근 heartbeat 정상",
        lastHeartbeatAt: "2026-07-16T08:59:55+09:00"
      },
      aggregation: {
        id: 12,
        status: "running",
        progressPercent: "25",
        totalTargetCount: 20,
        completedTargetCount: 5,
        runningTargetCount: 1,
        pendingTargetCount: 13,
        failedTargetCount: 1,
        items: []
      }
    }
  })
}));

afterEach(cleanup);

describe("시스템 관리 집계 워커", () => {
  it("실제 heartbeat 상태와 최신 시각, 모든 작업 대상 수를 표시한다", () => {
    render(<SystemManagement />);

    const card = screen.getByLabelText("캔들 집계 워커");
    expect(within(card).getByRole("heading", { name: "캔들 집계" })).toBeInTheDocument();
    expect(within(card).getByText("집계 워커")).toBeInTheDocument();
    expect(within(card).getByText("동작 중", { selector: "strong" })).toBeInTheDocument();
    expect(within(card).getByText(/마지막 heartbeat/)).toHaveTextContent("07. 16.");
    expect(within(card).getByText(/완료 5/)).toHaveTextContent("실행 1");
    expect(within(card).getByText(/대기 13/)).toHaveTextContent("실패 1");
  });
});
