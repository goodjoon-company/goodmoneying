import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  CreateDatasetBuildCommand,
  DatasetBuild,
  DatasetCoverage,
  DatasetSeriesResponse,
  DatasetVersion
} from "../../api";
import { DataLab } from "./DataLab";

const version11: DatasetVersion = {
  datasetVersionId: 11,
  schemaVersion: "dataset-v1",
  asOf: "2026-07-17T05:00:00Z",
  from: "2026-07-17T00:00:00Z",
  to: "2026-07-17T02:00:00Z",
  contentHash: "a".repeat(64),
  availabilityPolicy: "point_in_time_v1",
  fillPolicy: "none",
  missingPolicy: "fail",
  createdAt: "2026-07-17T06:00:02Z",
  series: [
    {
      seriesId: 101,
      instrumentId: 1,
      dataKind: "candle",
      unit: "1m",
      definitionSetHash: null,
      calculationVersion: "source-candle-v1"
    }
  ]
};

const version12: DatasetVersion = {
  ...version11,
  datasetVersionId: 12,
  contentHash: "b".repeat(64),
  series: [{ ...version11.series[0], seriesId: 202 }]
};

const build: DatasetBuild = {
  buildId: 7,
  requestId: "dataset-request-1",
  idempotencyKey: "dataset-key-1",
  actorId: "operator:test",
  requestedAt: "2026-07-17T06:00:00Z",
  frozenAt: "2026-07-17T06:00:01Z",
  status: "retry_wait",
  attemptCount: 2,
  maxAttempts: 3,
  nextRetryAt: "2026-07-17T06:05:00Z",
  deadLetterReason: null,
  datasetVersionId: null,
  errorCode: null,
  errorMessage: null
};

const coverage: DatasetCoverage = {
  datasetVersionId: 11,
  snapshotHash: "c".repeat(64),
  requestedBucketCount: 3,
  eligibleBucketCount: 2,
  usableRatio: "0.6667",
  counts: { available: 2, no_trade: 0, missing: 0, unavailable: 0, unverified: 1 },
  items: [
    {
      seriesId: 101,
      rangeStartAt: "2026-07-17T00:00:00Z",
      rangeEndAt: "2026-07-17T01:00:00Z",
      knowledgeAt: "2026-07-17T00:00:02Z",
      status: "available",
      bucketCount: 2
    },
    {
      seriesId: 101,
      rangeStartAt: "2026-07-17T01:00:00Z",
      rangeEndAt: "2026-07-17T02:00:00Z",
      knowledgeAt: "2026-07-17T06:00:01Z",
      status: "unverified",
      bucketCount: 1
    }
  ]
};

const series: DatasetSeriesResponse = {
  datasetVersionId: 11,
  seriesId: 101,
  dataKind: "candle",
  unit: "1m",
  items: [
    {
      occurredAt: "2026-07-17T00:00:00Z",
      knowledgeAt: "2026-07-17T00:00:02Z",
      quality: "available",
      contentHash: "d".repeat(64),
      values: { open: "100", close: "101" }
    }
  ],
  nextCursor: null
};

const mocks = vi.hoisted(() => ({
  createDatasetBuild: vi.fn(),
  loadDatasetBuilds: vi.fn(),
  loadDatasetVersions: vi.fn(),
  loadDatasetCoverage: vi.fn(),
  loadDatasetSeries: vi.fn()
}));

vi.mock("../../api", async () => {
  const actual = await vi.importActual<typeof import("../../api")>("../../api");
  return {
    ...actual,
    createDatasetBuild: mocks.createDatasetBuild,
    loadDataFoundation: vi.fn(async () => ({
      timeZone: "UTC",
      policyStartAt: "2024-01-01T00:00:00Z",
      summary: {
        marketCount: 1,
        krwMarketCount: 1,
        activeTargetCount: 4,
        pendingBackfillJobCount: 0,
        desiredSubscriptionCount: 1,
        coverageCounts: { available: 2, no_trade: 0, missing: 0, unavailable: 0, unverified: 0 }
      },
      markets: [
        {
          instrumentId: 1,
          marketCode: "KRW-BTC",
          koreanName: "비트코인",
          englishName: "Bitcoin",
          quoteCurrency: "KRW",
          tradingStatus: "active",
          marketWarning: "NONE",
          targetStatus: "active",
          activeDataTypeCount: 4,
          totalDataTypeCount: 4,
          coverageCounts: { available: 2, no_trade: 0, missing: 0, unavailable: 0, unverified: 0 },
          collectionPolicy: {
            startAt: "2024-01-01T00:00:00Z",
            dataTypes: ["source_candle", "trade_event", "orderbook_snapshot", "ticker_snapshot"],
            candleUnit: "1m",
            retentionDays: null,
            priority: 100,
            continuous: true
          }
        }
      ]
    })),
    loadDatasetBuilds: mocks.loadDatasetBuilds,
    loadDatasetVersions: mocks.loadDatasetVersions,
    loadDatasetCoverage: mocks.loadDatasetCoverage,
    loadDatasetSeries: mocks.loadDatasetSeries
  };
});

afterEach(() => {
  cleanup();
  Object.values(mocks).forEach((mock) => mock.mockReset());
});

describe("Data Lab", () => {
  beforeEach(() => {
    mocks.createDatasetBuild.mockResolvedValue(build);
    mocks.loadDatasetBuilds.mockResolvedValue({ items: [build], nextCursor: null });
    mocks.loadDatasetVersions.mockResolvedValue({ items: [version12, version11], nextCursor: null });
    mocks.loadDatasetCoverage.mockResolvedValue(coverage);
    mocks.loadDatasetSeries.mockResolvedValue(series);
  });

  it("build 상태, 불변 version, coverage, exact member와 A/B 비교 대상을 표시한다", async () => {
    renderDataLab();

    expect(await screen.findByRole("heading", { name: "Data Lab" })).toBeInTheDocument();
    expect(await screen.findByText("Build #7")).toBeInTheDocument();
    expect(screen.getByText("retry_wait")).toBeInTheDocument();
    expect(screen.getByText("Version #12")).toBeInTheDocument();
    expect(screen.getByText("Version #11")).toBeInTheDocument();
    expect(await screen.findByText("available 2")).toBeInTheDocument();
    expect(await screen.findByText("unverified 1")).toBeInTheDocument();
    expect(await screen.findByRole("img", { name: "series exact member chart" })).toBeInTheDocument();
    expect(await screen.findByRole("table", { name: "series exact member table" })).toBeInTheDocument();
    expect(await screen.findByText("open 100 · close 101")).toBeInTheDocument();
    expect(screen.getByText("A/B 1 · candle · 1m")).toBeInTheDocument();
  });

  it("선택한 version을 편집하지 않고 새 build로 복제한다", async () => {
    const user = userEvent.setup();
    renderDataLab();

    await user.click(await screen.findByRole("button", { name: "Version #12 복제" }));

    expect(mocks.createDatasetBuild).toHaveBeenCalledWith(
      expect.objectContaining({
        reason: "Data Lab version 12 복제",
        selection: expect.objectContaining({
          asOf: "2026-07-17T05:00:00.000Z",
          series: [
            {
              instrumentId: 1,
              dataKind: "candle",
              unit: "1m",
              definitionSetHash: null,
              calculationVersion: "source-candle-v1"
            }
          ]
        })
      })
    );
  });

  it("신규 build 생성은 KRW 시장과 KST 범위를 UTC 명령으로 보낸다", async () => {
    const user = userEvent.setup();
    renderDataLab();

    await user.clear(await screen.findByLabelText("작업자 ID"));
    await user.type(screen.getByLabelText("작업자 ID"), "operator:test");
    await user.clear(screen.getByLabelText("사유"));
    await user.type(screen.getByLabelText("사유"), "전략 연구");
    await user.clear(screen.getByLabelText("시작 KST"));
    await user.type(screen.getByLabelText("시작 KST"), "2026-07-17T09:00");
    await user.clear(screen.getByLabelText("종료 KST"));
    await user.type(screen.getByLabelText("종료 KST"), "2026-07-17T11:00");
    await user.click(screen.getByRole("button", { name: "신규 build 생성" }));

    expect(mocks.createDatasetBuild).toHaveBeenCalledWith(
      expect.objectContaining({
        actorId: "operator:test",
        reason: "전략 연구",
        selection: expect.objectContaining({
          from: "2026-07-17T00:00:00.000Z",
          to: "2026-07-17T02:00:00.000Z",
          series: [
            {
              instrumentId: 1,
              dataKind: "candle",
              unit: "1m",
              definitionSetHash: null,
              calculationVersion: "source-candle-v1"
            }
          ]
        })
      })
    );
  });

  it("cursor가 있는 build, version, series 다음 페이지를 누적 탐색한다", async () => {
    const user = userEvent.setup();
    const nextBuild: DatasetBuild = { ...build, buildId: 8, status: "pending", nextRetryAt: null };
    const nextVersion: DatasetVersion = {
      ...version11,
      datasetVersionId: 13,
      contentHash: "e".repeat(64),
      series: [{ ...version11.series[0], seriesId: 303 }]
    };
    mocks.loadDatasetBuilds.mockImplementation(async ({ cursor }: { cursor?: string | null } = {}) =>
      cursor === "build-next"
        ? { items: [nextBuild], nextCursor: null }
        : { items: [build], nextCursor: "build-next" }
    );
    mocks.loadDatasetVersions.mockImplementation(async ({ cursor }: { cursor?: string | null } = {}) =>
      cursor === "version-next"
        ? { items: [nextVersion], nextCursor: null }
        : { items: [version12], nextCursor: "version-next" }
    );
    mocks.loadDatasetSeries.mockImplementation(async ({ cursor }: { cursor?: string | null } = {}) =>
      cursor === "series-next"
        ? {
            ...series,
            items: [
              {
                ...series.items[0],
                occurredAt: "2026-07-17T00:01:00Z",
                contentHash: "f".repeat(64),
                values: { open: "102", close: "103" }
              }
            ],
            nextCursor: null
          }
        : { ...series, nextCursor: "series-next" }
    );
    renderDataLab();

    await user.click(await screen.findByRole("button", { name: "Build 더 보기" }));
    expect(await screen.findByText("Build #8")).toBeInTheDocument();
    expect(mocks.loadDatasetBuilds).toHaveBeenCalledWith({ pageSize: 50, cursor: "build-next" });

    await user.click(await screen.findByRole("button", { name: "Version 더 보기" }));
    expect(await screen.findByText("Version #13")).toBeInTheDocument();
    expect(mocks.loadDatasetVersions).toHaveBeenCalledWith({ pageSize: 50, cursor: "version-next" });

    await user.click(await screen.findByRole("button", { name: "Series 더 보기" }));
    expect(await screen.findByText("open 102 · close 103")).toBeInTheDocument();
    expect(mocks.loadDatasetSeries).toHaveBeenCalledWith(
      expect.objectContaining({ cursor: "series-next", pageSize: 500 })
    );
  });

  it("다중 series version에서 조회할 exact member series를 선택한다", async () => {
    const user = userEvent.setup();
    const multiSeriesVersion: DatasetVersion = {
      ...version12,
      series: [
        { ...version12.series[0], seriesId: 202 },
        { ...version12.series[0], seriesId: 404, instrumentId: 2 }
      ]
    };
    mocks.loadDatasetVersions.mockResolvedValue({ items: [multiSeriesVersion], nextCursor: null });
    renderDataLab();

    await user.selectOptions(await screen.findByLabelText("Series"), "404");

    expect(mocks.loadDatasetSeries).toHaveBeenLastCalledWith(
      expect.objectContaining({
        datasetVersionId: 12,
        seriesId: 404,
        pageSize: 500
      })
    );
  });
});

function renderDataLab() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <DataLab />
    </QueryClientProvider>
  );
}
