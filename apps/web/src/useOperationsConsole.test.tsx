import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useOperationsConsole } from "./useOperationsConsole";
import {
  createTestBackfillJob,
  createTestInstrumentDetail,
  createTestOperationsSnapshot
} from "./testOperationsApi";
import type { OperationsDataClient } from "./operationsData";

function Harness() {
  const dataClient: OperationsDataClient = {
    loadOperationsSnapshot: async () => createTestOperationsSnapshot(),
    loadCandidateUniverse: async () => [],
    loadMarketList: async () => [],
    loadCollectionCoverageSegments: async () => [],
    loadInstrumentSnapshot: async (instrumentId) => ({
      detail: createTestInstrumentDetail(instrumentId),
      candles: []
    }),
    updateCollectionTargets: async () => undefined,
    createBackfillPlan: async () => ({
      planId: "plan-1",
      dataType: "source_candle",
      estimatedRequestCount: 1,
      estimatedRowCount: 1,
      estimatedStorageBytes: 1,
      targets: [1]
    }),
    startBackfillJob: async () => createTestBackfillJob({ id: 1, status: "pending" }),
    controlBackfillJob: async () => createTestBackfillJob({ id: 1, status: "running" }),
    deleteBackfillJob: async () => undefined
  };
  const consoleState = useOperationsConsole({
    dataClient,
    refetchOnDashboard: false
  });

  if (!consoleState.snapshot) return <span>loading</span>;

  return (
    <section>
      <strong>{consoleState.snapshot.dashboard.status}</strong>
      <span>선택 {consoleState.selectedInstrumentId}</span>
      <span>상세 {consoleState.isDetailOpen ? "열림" : "닫힘"}</span>
      <button type="button" onClick={() => void consoleState.openInstrumentDetail(3)}>
        3번 상세
      </button>
    </section>
  );
}

function RefetchHarness({
  dataClient
}: {
  dataClient: OperationsDataClient;
}) {
  const consoleState = useOperationsConsole({ dataClient });

  if (!consoleState.snapshot) return <span>loading</span>;

  return (
    <section>
      <strong>{consoleState.snapshot.dashboard.status}</strong>
      <button type="button" onClick={() => consoleState.setActiveSection("targets")}>
        Backfill 관리
      </button>
    </section>
  );
}

describe("운영 화면 상태 Module", () => {
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("첫 스냅샷의 첫 거래 상품을 선택하고 상세 열기 상태를 관리한다", async () => {
    const user = userEvent.setup();
    const queryClient = new QueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <Harness />
      </QueryClientProvider>
    );

    await waitFor(() => expect(screen.getByText("normal")).toBeInTheDocument());
    expect(screen.getByText("선택 1")).toBeInTheDocument();
    expect(screen.getByText("상세 닫힘")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "3번 상세" }));

    expect(await screen.findByText("선택 3")).toBeInTheDocument();
    expect(screen.getByText("상세 열림")).toBeInTheDocument();
  });

  it("Backfill 관리 화면에서는 계획별 진행률을 10초 주기로 다시 조회한다", async () => {
    const loadOperationsSnapshot = vi.fn(async () => createTestOperationsSnapshot());
    const dataClient: OperationsDataClient = {
      loadOperationsSnapshot,
      loadCandidateUniverse: async () => [],
      loadMarketList: async () => [],
      loadCollectionCoverageSegments: async () => [],
      loadInstrumentSnapshot: async (instrumentId) => ({
        detail: createTestInstrumentDetail(instrumentId),
        candles: []
      }),
      updateCollectionTargets: async () => undefined,
      createBackfillPlan: async () => ({
        planId: "plan-1",
        dataType: "source_candle",
        estimatedRequestCount: 1,
        estimatedRowCount: 1,
        estimatedStorageBytes: 1,
        targets: [1]
      }),
      startBackfillJob: async () => createTestBackfillJob({ id: 1, status: "pending" }),
      controlBackfillJob: async () => createTestBackfillJob({ id: 1, status: "running" }),
      deleteBackfillJob: async () => undefined
    };
    const queryClient = new QueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <RefetchHarness dataClient={dataClient} />
      </QueryClientProvider>
    );

    await waitFor(() => expect(screen.getByText("normal")).toBeInTheDocument());
    expect(loadOperationsSnapshot).toHaveBeenCalledTimes(1);

    vi.useFakeTimers();
    fireEvent.click(screen.getByRole("button", { name: "Backfill 관리" }));
    await vi.advanceTimersByTimeAsync(10_000);

    expect(loadOperationsSnapshot).toHaveBeenCalledTimes(2);
  });
});
