import type { CandidateUniverseEntry } from "./api";

export type SortMode = "trade";

export function initialSelectedInstrumentIds(
  entries: CandidateUniverseEntry[],
  preferredOrderIds: number[] = []
): Set<number> {
  if (preferredOrderIds.length > 0) {
    return new Set(preferredOrderIds);
  }
  return new Set(
    orderedSelectedInstrumentIds(
      entries,
      new Set(entries.filter((entry) => entry.selected).map((entry) => entry.instrument.id))
    )
  );
}

export function orderedSelectedInstrumentIds(
  entries: CandidateUniverseEntry[],
  selectedIds: Set<number>,
  preferredOrderIds: number[] = []
): number[] {
  const preferredSelectedIds = preferredOrderIds.filter((id) => selectedIds.has(id));
  const preferredSelectedIdSet = new Set(preferredSelectedIds);
  const selectedEntries = entries.filter((entry) => selectedIds.has(entry.instrument.id));
  const orderedFavoriteIds = selectedEntries
    .filter((entry) => entry.favoriteOrder !== null)
    .sort((left, right) => Number(left.favoriteOrder) - Number(right.favoriteOrder))
    .map((entry) => entry.instrument.id)
    .filter((id) => !preferredSelectedIdSet.has(id));
  const orderedIdSet = new Set([...preferredSelectedIds, ...orderedFavoriteIds]);
  const appendedIds = [...selectedIds].filter((id) => !orderedIdSet.has(id));
  return [...preferredSelectedIds, ...orderedFavoriteIds, ...appendedIds];
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
      void sortMode;
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
