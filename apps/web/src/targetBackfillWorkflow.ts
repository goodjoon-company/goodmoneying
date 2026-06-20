import type { BackfillPlan, CandidateUniverseEntry, Status } from "./api";

export type SortMode = "trade" | "quality";

export type BackfillDraftPlan = BackfillPlan & {
  targetStartAt: string;
  targetEndAt: string;
};

const QUALITY_RANK: Record<Status, number> = {
  normal: 0,
  warning: 1,
  incident: 2
};

export function initialSelectedInstrumentIds(entries: CandidateUniverseEntry[]): Set<number> {
  return new Set(
    entries.filter((entry) => entry.selected).map((entry) => entry.instrument.id)
  );
}

export function filterAndSortCandidateEntries(
  entries: CandidateUniverseEntry[],
  searchText: string,
  sortMode: SortMode
): CandidateUniverseEntry[] {
  const normalizedSearch = searchText.trim().toLocaleLowerCase("ko-KR");
  return [...entries]
    .filter((entry) => {
      if (!normalizedSearch) return true;
      const text = [
        entry.instrument.baseAsset,
        entry.instrument.quoteCurrency,
        entry.instrument.displayName ?? ""
      ]
        .join(" ")
        .toLocaleLowerCase("ko-KR");
      return text.includes(normalizedSearch);
    })
    .sort((left, right) => {
      if (sortMode === "quality") {
        return (
          QUALITY_RANK[left.qualityStatus] - QUALITY_RANK[right.qualityStatus] ||
          Number(right.accTradePrice24h) - Number(left.accTradePrice24h)
        );
      }
      return Number(right.accTradePrice24h) - Number(left.accTradePrice24h);
    });
}

export function toggleSelectedInstrument(
  selectedIds: Set<number>,
  instrumentId: number,
  limit = 50
): Set<number> {
  const next = new Set(selectedIds);
  if (next.has(instrumentId)) {
    next.delete(instrumentId);
  } else if (next.size < limit) {
    next.add(instrumentId);
  }
  return next;
}

export function canSaveTargets(selectedCount: number, isPending: boolean, limit = 50): boolean {
  return selectedCount <= limit && !isPending;
}

export function canCreateBackfillPlan(
  selectedCount: number,
  isPending: boolean,
  limit = 50
): boolean {
  return selectedCount > 0 && selectedCount <= limit && !isPending;
}

export function canApproveBackfillPlans(planCount: number, isPending: boolean): boolean {
  return planCount > 0 && !isPending;
}

export function addDraftBackfillPlan(
  current: BackfillDraftPlan[],
  plan: BackfillPlan,
  range: { targetStartAt: string; targetEndAt: string }
): BackfillDraftPlan[] {
  return [...current, { ...plan, ...range }];
}

export function removeDraftBackfillPlan(
  current: BackfillDraftPlan[],
  planId: string
): BackfillDraftPlan[] {
  return current.filter((item) => item.planId !== planId);
}

export function sumDraftBackfillPlans(
  plans: BackfillDraftPlan[],
  key: "estimatedRequestCount" | "estimatedStorageBytes"
): number {
  return plans.reduce((total, plan) => total + plan[key], 0);
}
