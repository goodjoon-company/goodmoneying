import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { BacktestRun, BacktestRunSummary } from "../../api";
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

const runSummaries: BacktestRunSummary[] = [
  {
    backtestRunId: 22,
    strategyVersionId: 41,
    datasetVersionId: 12,
    engineVersion: "backtest-core-v1",
    status: "succeeded",
    inputHash: "e".repeat(64),
    resultHash: "f".repeat(64),
    requestedAt: "2026-07-18T00:00:00Z",
    startedAt: "2026-07-18T00:00:00Z",
    finishedAt: "2026-07-18T00:00:00Z"
  },
  {
    backtestRunId: 21,
    strategyVersionId: 41,
    datasetVersionId: 12,
    engineVersion: "backtest-core-v1",
    status: "succeeded",
    inputHash: "e".repeat(64),
    resultHash: "f".repeat(64),
    requestedAt: "2026-07-18T00:00:00Z",
    startedAt: "2026-07-18T00:00:00Z",
    finishedAt: "2026-07-18T00:00:00Z"
  }
];

const nextRunSummary: BacktestRunSummary = {
  backtestRunId: 20,
  strategyVersionId: 41,
  datasetVersionId: 12,
  engineVersion: "backtest-core-v1",
  status: "succeeded",
  inputHash: "e".repeat(64),
  resultHash: "f".repeat(64),
  requestedAt: "2026-07-18T00:00:00Z",
  startedAt: "2026-07-18T00:00:00Z",
  finishedAt: "2026-07-18T00:00:00Z"
};

const mocks = vi.hoisted(() => ({
  loadBacktestRun: vi.fn(),
  loadBacktestRuns: vi.fn()
}));

vi.mock("../../api", async () => {
  const actual = await vi.importActual<typeof import("../../api")>("../../api");
  return {
    ...actual,
    loadBacktestRun: mocks.loadBacktestRun,
    loadBacktestRuns: mocks.loadBacktestRuns
  };
});

beforeEach(() => {
  mocks.loadBacktestRun.mockImplementation(async (backtestRunId: number) => ({
    ...run,
    backtestRunId
  }));
  mocks.loadBacktestRuns.mockResolvedValue({ items: runSummaries, nextCursor: null });
});

afterEach(() => {
  cleanup();
  mocks.loadBacktestRun.mockReset();
  mocks.loadBacktestRuns.mockReset();
});

describe("Backtest Lab", () => {
  it("저장된 백테스트 run의 성과, 체결, 산출물을 읽기 전용으로 표시한다", async () => {
    renderBacktestLab();

    expect(await screen.findByRole("heading", { name: "Backtest Lab" })).toBeInTheDocument();
    expect(await screen.findByText("Run #22")).toBeInTheDocument();
    expect(screen.getAllByText("succeeded").length).toBeGreaterThan(0);
    expect(screen.getByText("finalEquity")).toBeInTheDocument();
    expect(screen.getAllByText("1009.579790")).toHaveLength(2);
    expect(screen.getByRole("table", { name: "백테스트 체결 결과" })).toHaveTextContent(
      "partially_filled"
    );
    expect(screen.getByText("walk_forward_summary")).toBeInTheDocument();
    expect(screen.getByText(run.resultHash ?? "")).toBeInTheDocument();
  });

  it("저장된 run 목록을 먼저 읽고 선택한 run 상세만 조회한다", async () => {
    const user = userEvent.setup();
    renderBacktestLab();

    expect(await screen.findByRole("region", { name: "저장된 백테스트 run 목록" }))
      .toBeInTheDocument();
    expect(mocks.loadBacktestRuns).toHaveBeenCalledWith({ pageSize: 25, cursor: null });

    await user.click(await screen.findByRole("button", { name: "Run #21 선택" }));

    expect(mocks.loadBacktestRun).toHaveBeenLastCalledWith(21);
    expect(screen.queryByRole("button", { name: "백테스트 실행" })).not.toBeInTheDocument();
  });

  it("목록 nextCursor가 있으면 다음 run 페이지를 불러와 누적 선택한다", async () => {
    const user = userEvent.setup();
    mocks.loadBacktestRuns.mockImplementation(async (options: { cursor?: string | null }) =>
      options.cursor === "cursor-2"
        ? { items: [nextRunSummary], nextCursor: null }
        : { items: runSummaries, nextCursor: "cursor-2" }
    );

    renderBacktestLab();

    await user.click(await screen.findByRole("button", { name: "다음 run 목록 불러오기" }));

    expect(mocks.loadBacktestRuns).toHaveBeenLastCalledWith({
      pageSize: 25,
      cursor: "cursor-2"
    });
    expect(await screen.findByRole("button", { name: "Run #20 선택" })).toBeInTheDocument();
  });

  it("run ID를 바꿔 조회하되 실행 생성 명령은 보내지 않는다", async () => {
    const user = userEvent.setup();
    renderBacktestLab();

    await user.click(await screen.findByLabelText("백테스트 Run ID"));
    await user.keyboard("{Control>}a{/Control}23");
    await user.click(screen.getByRole("button", { name: "Run 조회" }));

    expect(mocks.loadBacktestRun).toHaveBeenLastCalledWith(23);
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
