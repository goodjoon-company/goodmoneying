import {
  controlBackfillJob,
  createBackfillPlan,
  deleteBackfillJob,
  loadCandidateUniverse,
  loadCollectionCoverageSegments,
  loadInstrumentSnapshot,
  loadMarketList,
  loadOperationsSnapshot,
  subscribeDashboardSummary,
  subscribeMarketList,
  startBackfillJob,
  updateCollectionTargets,
  type BackfillJob,
  type BackfillPlan,
  type CandidateUniverseEntry,
  type CoverageSegment,
  type CreateBackfillPlanOptions,
  type MarketListRow,
  type DashboardSummary,
  type OperationsSnapshot
} from "./api";

export type OperationsDataClient = {
  loadOperationsSnapshot: () => Promise<OperationsSnapshot>;
  subscribeDashboardSummary?: (handler: (dashboard: DashboardSummary) => void) => () => void;
  subscribeMarketList?: (handler: (rows: MarketListRow[]) => void) => () => void;
  loadCandidateUniverse: () => Promise<CandidateUniverseEntry[]>;
  loadMarketList: () => Promise<MarketListRow[]>;
  loadCollectionCoverageSegments: (instrumentId: number) => Promise<CoverageSegment[]>;
  loadInstrumentSnapshot: (instrumentId: number) => Promise<{
    detail: NonNullable<OperationsSnapshot["detail"]>;
    candles: OperationsSnapshot["candles"];
  }>;
  updateCollectionTargets: (instrumentIds: number[]) => Promise<void>;
  createBackfillPlan: (
    instrumentIds: number[],
    options?: CreateBackfillPlanOptions
  ) => Promise<BackfillPlan>;
  startBackfillJob: (
    instrumentIds: number[],
    options?: CreateBackfillPlanOptions
  ) => Promise<BackfillJob>;
  controlBackfillJob: (jobId: number, action: string) => Promise<BackfillJob>;
  deleteBackfillJob: (jobId: number) => Promise<void>;
};

export type HttpOperationsDataClientOptions = {
  apiBaseUrl?: string;
  operatorToken?: string;
};

export function createHttpOperationsDataClient(
  _options: HttpOperationsDataClientOptions = {}
): OperationsDataClient {
  return {
    loadOperationsSnapshot,
    subscribeDashboardSummary,
    subscribeMarketList,
    loadCandidateUniverse,
    loadMarketList,
    loadCollectionCoverageSegments,
    loadInstrumentSnapshot,
    updateCollectionTargets,
    createBackfillPlan,
    startBackfillJob,
    controlBackfillJob,
    deleteBackfillJob
  };
}
