import { describe, expect, it } from "vitest";
import type { CandidateUniverseEntry } from "./api";
import {
  canCreateBackfillPlan,
  canSaveTargets,
  filterAndSortCandidateEntries,
  initialSelectedInstrumentIds,
  orderedSelectedInstrumentIds,
  toggleSelectedInstrument
} from "./targetBackfillWorkflow";

function entry(
  id: number,
  baseAsset: string,
  selected: boolean,
  accTradePrice24h: string,
  qualityStatus: CandidateUniverseEntry["qualityStatus"] = "normal",
  favoriteOrder: number | null = selected ? id : null
): CandidateUniverseEntry {
  return {
    instrument: {
      id,
      exchange: "UPBIT",
      marketCode: `KRW-${baseAsset}`,
      quoteCurrency: "KRW",
      baseAsset,
      displayName: `${baseAsset} 이름`
    },
    rank: id,
    accTradePrice24h,
    accTradePrice24hDisplay: `₩${accTradePrice24h}`,
    selected,
    favoriteOrder,
    candidateStatus: "in_universe",
    qualityStatus,
    qualityDetail: qualityStatus,
    collectionRangeDisplay: "2026-01-01 00:00 KST ~ NOW",
    collectedStartAt: "2026-01-01T00:00:00+09:00",
    collectedEndAt: "2026-06-19T09:00:00+09:00",
    isRealtimeTarget: selected
  };
}

describe("수집 대상과 백필 workflow", () => {
  it("후보 유니버스에서 초기 활성 수집 대상 Set을 만든다", () => {
    const ids = initialSelectedInstrumentIds([
      entry(1, "BTC", true, "100"),
      entry(2, "ETH", false, "90"),
      entry(3, "XRP", true, "80")
    ]);

    expect([...ids]).toEqual([1, 3]);
  });

  it("Backfill 저장 요청은 기존 관심목록 순서를 보존하고 신규 선택을 뒤에 붙인다", () => {
    const entries = [
      entry(1, "BTC", true, "100", "normal", 2),
      entry(2, "ETH", false, "90", "normal", null),
      entry(3, "XRP", true, "80", "normal", 1)
    ];
    const selectedIds = new Set([1, 3, 2]);

    expect(orderedSelectedInstrumentIds(entries, selectedIds)).toEqual([3, 1, 2]);
  });

  it("거래대금순으로 후보를 검색하고 정렬한다", () => {
    const entries = [
      entry(1, "BTC", true, "100", "normal"),
      entry(2, "ETH", true, "200", "warning"),
      entry(3, "SOL", false, "50", "incident")
    ];

    expect(filterAndSortCandidateEntries(entries, "", "trade").map((item) => item.instrument.id))
      .toEqual([2, 1, 3]);
    expect(filterAndSortCandidateEntries(entries, "sol", "trade").map((item) => item.instrument.id))
      .toEqual([3]);
  });

  it("활성 수집 대상은 최대 50개까지만 추가하고 기존 선택은 해제한다", () => {
    const full = new Set(Array.from({ length: 50 }, (_, index) => index + 1));

    expect(toggleSelectedInstrument(full, 51).has(51)).toBe(false);
    expect(toggleSelectedInstrument(full, 1).has(1)).toBe(false);
  });

  it("저장과 백필 버튼 가능 상태를 workflow 규칙으로 계산한다", () => {
    expect(canSaveTargets(50, false)).toBe(true);
    expect(canSaveTargets(51, false)).toBe(false);
    expect(canSaveTargets(50, true)).toBe(false);
    expect(canCreateBackfillPlan(1, false)).toBe(true);
    expect(canCreateBackfillPlan(0, false)).toBe(false);
  });
});
