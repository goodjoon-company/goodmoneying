import { describe, expect, it } from "vitest";
import type { CandidateUniverseEntry } from "./api";
import {
  addDraftBackfillPlan,
  canApproveBackfillPlans,
  canCreateBackfillPlan,
  canSaveTargets,
  filterAndSortCandidateEntries,
  initialSelectedInstrumentIds,
  removeDraftBackfillPlan,
  sumDraftBackfillPlans,
  toggleSelectedInstrument
} from "./targetBackfillWorkflow";

function entry(
  id: number,
  baseAsset: string,
  selected: boolean,
  accTradePrice24h: string,
  qualityStatus: CandidateUniverseEntry["qualityStatus"] = "normal"
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
    candidateStatus: "in_universe",
    qualityStatus,
    qualityDetail: qualityStatus,
    collectionRangeDisplay: "2026-01-01 00:00 KST ~ NOW"
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

  it("거래대금순과 품질순으로 후보를 검색하고 정렬한다", () => {
    const entries = [
      entry(1, "BTC", true, "100", "normal"),
      entry(2, "ETH", true, "200", "warning"),
      entry(3, "SOL", false, "50", "incident")
    ];

    expect(filterAndSortCandidateEntries(entries, "", "trade").map((item) => item.instrument.id))
      .toEqual([2, 1, 3]);
    expect(filterAndSortCandidateEntries(entries, "", "quality").map((item) => item.instrument.id))
      .toEqual([1, 2, 3]);
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
    expect(canApproveBackfillPlans(1, false)).toBe(true);
    expect(canApproveBackfillPlans(0, false)).toBe(false);
  });

  it("백필 draft 계획을 추가, 삭제, 합산한다", () => {
    const draft = addDraftBackfillPlan(
      [],
      {
        planId: "plan-1",
        dataType: "source_candle",
        estimatedRequestCount: 12,
        estimatedRowCount: 2880,
        estimatedStorageBytes: 737280,
        targets: [1, 2]
      },
      {
        targetStartAt: "2026-01-01T00:00:00.000Z",
        targetEndAt: "2026-01-03T00:00:00.000Z"
      }
    );

    expect(sumDraftBackfillPlans(draft, "estimatedRequestCount")).toBe(12);
    expect(sumDraftBackfillPlans(draft, "estimatedStorageBytes")).toBe(737280);
    expect(removeDraftBackfillPlan(draft, "plan-1")).toEqual([]);
  });
});
