import type { CandidateUniverseEntry } from "./api";

export type SortMode = "trade";

export function initialSelectedInstrumentIds(entries: CandidateUniverseEntry[]): Set<number> {
  return new Set(
    orderedSelectedInstrumentIds(
      entries,
      new Set(entries.filter((entry) => entry.selected).map((entry) => entry.instrument.id))
    )
  );
}

export function orderedSelectedInstrumentIds(
  entries: CandidateUniverseEntry[],
  selectedIds: Set<number>
): number[] {
  const selectedEntries = entries.filter((entry) => selectedIds.has(entry.instrument.id));
  const orderedFavoriteIds = selectedEntries
    .filter((entry) => entry.favoriteOrder !== null)
    .sort((left, right) => Number(left.favoriteOrder) - Number(right.favoriteOrder))
    .map((entry) => entry.instrument.id);
  const orderedFavoriteIdSet = new Set(orderedFavoriteIds);
  const appendedIds = [...selectedIds].filter((id) => !orderedFavoriteIdSet.has(id));
  return [...orderedFavoriteIds, ...appendedIds];
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
