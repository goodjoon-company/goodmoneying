import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { BacktestRun } from "../../api";
import { BacktestLab } from "./BacktestLab";

const run: BacktestRun = {
  backtestRunId: 21,
  strategyVersionId: 41,
  datasetVersionId: 12,
  status: "succeeded",
  inputHash: "e".repeat(64),
  resultHash: "f".repeat(64),
  metrics: [
    {
      metricName: "finalEquity",
      scopeKey: "run",
      metricValue: "1009.579790",
      metricPayload: {}
    }
  ],
  trades: [
    {
      tradeSequence: 1,
      side: "buy",
      requestedQuantity: "3",
      filledQuantity: "1.00",
      remainingQuantity: "2.00",
      fillPrice: "100.100",
      feePaid: "0.100100",
      status: "partially_filled",
      occurredAt: "2026-07-18T00:00:00Z",
      knowledgeAt: "2026-07-18T00:00:00Z"
    }
  ],
  artifacts: [
    {
      artifactType: "walk_forward_summary",
      contentHash: "c".repeat(64),
      mediaType: "application/json",
      storageUri: "artifact://p4-3/walk-forward",
      metadata: { folds: 3 }
    }
  ]
};

const mocks = vi.hoisted(() => ({
  loadBacktestRun: vi.fn()
}));

vi.mock("../../api", async () => {
  const actual = await vi.importActual<typeof import("../../api")>("../../api");
  return {
    ...actual,
    loadBacktestRun: mocks.loadBacktestRun
  };
});

beforeEach(() => {
  mocks.loadBacktestRun.mockResolvedValue(run);
});

afterEach(() => {
  cleanup();
  mocks.loadBacktestRun.mockReset();
});

describe("Backtest Lab", () => {
  it("저장된 백테스트 run의 성과, 체결, 산출물을 읽기 전용으로 표시한다", async () => {
    renderBacktestLab();

    expect(await screen.findByRole("heading", { name: "Backtest Lab" })).toBeInTheDocument();
    expect(await screen.findByText("Run #21")).toBeInTheDocument();
    expect(screen.getByText("succeeded")).toBeInTheDocument();
    expect(screen.getByText("finalEquity")).toBeInTheDocument();
    expect(screen.getAllByText("1009.579790")).toHaveLength(2);
    expect(screen.getByRole("table", { name: "백테스트 체결 결과" })).toHaveTextContent(
      "partially_filled"
    );
    expect(screen.getByText("walk_forward_summary")).toBeInTheDocument();
    expect(screen.getByText(run.resultHash)).toBeInTheDocument();
  });

  it("run ID를 바꿔 조회하되 실행 생성 명령은 보내지 않는다", async () => {
    const user = userEvent.setup();
    renderBacktestLab();

    await user.clear(await screen.findByLabelText("백테스트 Run ID"));
    await user.type(screen.getByLabelText("백테스트 Run ID"), "22");
    await user.click(screen.getByRole("button", { name: "Run 조회" }));

    expect(mocks.loadBacktestRun).toHaveBeenLastCalledWith(22);
    expect(screen.queryByRole("button", { name: "백테스트 실행" })).not.toBeInTheDocument();
  });
});

function renderBacktestLab() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } }
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <BacktestLab />
    </QueryClientProvider>
  );
}
