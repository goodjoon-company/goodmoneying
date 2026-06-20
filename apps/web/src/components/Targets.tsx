import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, ListChecks, Search, Settings2, X } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  approveBackfillJob,
  createBackfillPlan,
  loadCandidateUniverse,
  updateCollectionTargets,
  type CandidateUniverseEntry,
  type OperationsSnapshot
} from "../api";
import { formatBytes, formatDateTimeRange, dateTimeLocalToUtcIso } from "../operationsDisplay";
import {
  addDraftBackfillPlan,
  canApproveBackfillPlans,
  canCreateBackfillPlan,
  canSaveTargets,
  filterAndSortCandidateEntries,
  initialSelectedInstrumentIds,
  removeDraftBackfillPlan,
  sumDraftBackfillPlans,
  toggleSelectedInstrument,
  type BackfillDraftPlan,
  type SortMode
} from "../targetBackfillWorkflow";
import { InstrumentName, MiniMetric, statusText } from "./common";

const EMPTY_CANDIDATE_ENTRIES: CandidateUniverseEntry[] = [];
const DEFAULT_BACKFILL_START_INPUT = "2026-01-01T00:00";
const DEFAULT_BACKFILL_END_INPUT = "2026-02-01T00:00";

export function Targets({ snapshot }: { snapshot: OperationsSnapshot }) {
  const queryClient = useQueryClient();
  const [isBackfillDialogOpen, setBackfillDialogOpen] = useState(false);
  const [pendingPlans, setPendingPlans] = useState<BackfillDraftPlan[]>([]);
  const [approvedJobs, setApprovedJobs] = useState<number[]>([]);
  const [searchText, setSearchText] = useState("");
  const [sortMode, setSortMode] = useState<SortMode>("trade");
  const universeQuery = useQuery({
    queryKey: ["candidate-universe"],
    queryFn: loadCandidateUniverse,
    enabled: snapshot.source === "api"
  });
  const entries =
    snapshot.source === "api"
      ? universeQuery.data ?? EMPTY_CANDIDATE_ENTRIES
      : snapshot.candidateEntries;
  const visibleEntries = useMemo(
    () => filterAndSortCandidateEntries(entries, searchText, sortMode),
    [entries, searchText, sortMode]
  );
  const [selectedIds, setSelectedIds] = useState<Set<number>>(
    () => initialSelectedInstrumentIds(entries)
  );
  useEffect(() => {
    setSelectedIds(initialSelectedInstrumentIds(entries));
  }, [entries]);
  const mutation = useMutation({
    mutationFn: (ids: number[]) => updateCollectionTargets(ids),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["operations"] });
      void queryClient.invalidateQueries({ queryKey: ["candidate-universe"] });
    }
  });
  const createPlanMutation = useMutation({
    mutationFn: (options: { targetStartAt: string; targetEndAt: string }) =>
      createBackfillPlan(Array.from(selectedIds), options),
    onSuccess: (plan, variables) => {
      setPendingPlans((current) => addDraftBackfillPlan(current, plan, variables));
      setBackfillDialogOpen(false);
    }
  });
  const approvePlansMutation = useMutation({
    mutationFn: async (plans: BackfillDraftPlan[]) => {
      const jobs = [];
      for (const plan of plans) {
        jobs.push(await approveBackfillJob(plan.planId));
      }
      return jobs;
    },
    onSuccess: (jobs) => {
      setApprovedJobs(jobs.map((job) => job.id));
      setPendingPlans([]);
      void queryClient.invalidateQueries({ queryKey: ["operations"] });
    }
  });
  const selected = selectedIds.size;
  const canSave = canSaveTargets(selected, mutation.isPending);
  const canCreatePlan =
    canCreateBackfillPlan(selected, createPlanMutation.isPending);
  const canApprovePlans =
    canApproveBackfillPlans(pendingPlans.length, approvePlansMutation.isPending);
  const toggle = (instrumentId: number) => {
    setSelectedIds((previous) => toggleSelectedInstrument(previous, instrumentId));
  };
  return (
    <section className="split-page">
      <section className="panel">
        <div className="panel-heading">
          <h2>후보 유니버스 상위 100개</h2>
          <span>선택 {selected}/50</span>
        </div>
        <div className="target-toolbar">
          <label>
            <Search size={16} />
            <input
              placeholder="코인명 또는 심볼 검색"
              value={searchText}
              onChange={(event) => setSearchText(event.target.value)}
            />
          </label>
          <select
            aria-label="후보 정렬"
            value={sortMode}
            onChange={(event) => setSortMode(event.target.value as SortMode)}
          >
            <option value="trade">거래대금순</option>
            <option value="quality">품질순</option>
          </select>
          <button
            type="button"
            disabled={!canCreatePlan}
            onClick={() => setBackfillDialogOpen(true)}
          >
            <ListChecks size={16} />
            백필 계획 생성
          </button>
          <button type="button" disabled={!canSave} onClick={() => mutation.mutate(Array.from(selectedIds))}>
            <CheckCircle2 size={16} />
            저장
          </button>
        </div>
        {mutation.isError ? <p className="error-text">수집 대상 저장에 실패했습니다.</p> : null}
        {createPlanMutation.isError ? <p className="error-text">백필 계획 생성에 실패했습니다.</p> : null}
        <div className="target-table">
          <div className="target-table-head">
            <span>활성</span>
            <span>후보</span>
            <span>거래대금</span>
            <span>품질</span>
            <span>수집 범위</span>
          </div>
          {entries.length === 0 ? <p className="helper-text">후보 유니버스를 불러오는 중입니다.</p> : null}
          {entries.length > 0 && visibleEntries.length === 0 ? (
            <p className="helper-text">검색 조건에 맞는 후보가 없습니다.</p>
          ) : null}
          {visibleEntries.slice(0, 100).map((entry) => (
            <label className="target-row" key={entry.instrument.id}>
              <span>
                <input
                  type="checkbox"
                  checked={selectedIds.has(entry.instrument.id)}
                  onChange={() => toggle(entry.instrument.id)}
                />
                수집
              </span>
              <InstrumentName instrument={entry.instrument} />
              <strong>{entry.accTradePrice24hDisplay}</strong>
              <em className={`quality ${entry.qualityStatus}`} title={entry.qualityDetail}>
                {statusText(entry.qualityStatus)}
              </em>
              <span>{entry.collectionRangeDisplay}</span>
            </label>
          ))}
        </div>
      </section>
      <section className="panel side-panel">
        <div className="panel-heading">
          <h2>백필 승인 패널</h2>
          <Settings2 size={18} />
        </div>
        <MiniMetric
          label="예상 요청 수"
          value={sumDraftBackfillPlans(pendingPlans, "estimatedRequestCount").toLocaleString("ko-KR")}
          detail="1분 캔들 기준"
        />
        <MiniMetric
          label="예상 저장량"
          value={formatBytes(sumDraftBackfillPlans(pendingPlans, "estimatedStorageBytes"))}
          detail="중복 기간 요청 제외"
        />
        <MiniMetric
          label="감사 로그"
          value={`대상 변경 ${snapshot.dashboard.auditLogSummary.targetChangeCount24h}건`}
          detail={`${snapshot.dashboard.auditLogSummary.latestChangeLabel} · 최근 24시간`}
        />
        <div className="backfill-plan-list" aria-label="백필 계획 목록">
          {pendingPlans.length === 0 ? (
            <p className="helper-text">선택 코인으로 백필 계획을 생성하면 승인 대기 목록에 표시됩니다.</p>
          ) : null}
          {pendingPlans.map((plan) => (
            <article className="backfill-plan-card" key={plan.planId}>
              <div>
                <strong>계획 {plan.planId}</strong>
                <button
                  className="icon-button small"
                  type="button"
                  aria-label={`계획 ${plan.planId} 삭제`}
                  onClick={() =>
                    setPendingPlans((current) => removeDraftBackfillPlan(current, plan.planId))
                  }
                >
                  <X size={14} />
                </button>
              </div>
              <span>대상 {plan.targets.length}개</span>
              <em>
                {formatDateTimeRange(plan.targetStartAt, plan.targetEndAt)}
              </em>
              <span>
                요청 {plan.estimatedRequestCount.toLocaleString("ko-KR")} · 행{" "}
                {plan.estimatedRowCount.toLocaleString("ko-KR")}
              </span>
            </article>
          ))}
        </div>
        {approvedJobs.length > 0 ? (
          <p className="success-text">승인된 작업 {approvedJobs.join(", ")}</p>
        ) : null}
        {approvePlansMutation.isError ? <p className="error-text">백필 계획 승인에 실패했습니다.</p> : null}
        <button
          className="approve-backfill-button"
          type="button"
          disabled={!canApprovePlans}
          onClick={() => approvePlansMutation.mutate(pendingPlans)}
        >
          백필 계획 승인
        </button>
      </section>
      {isBackfillDialogOpen ? (
        <BackfillPlanDialog
          selectedCount={selected}
          isPending={createPlanMutation.isPending}
          onClose={() => setBackfillDialogOpen(false)}
          onConfirm={(range) => createPlanMutation.mutate(range)}
        />
      ) : null}
    </section>
  );
}

function BackfillPlanDialog({
  selectedCount,
  isPending,
  onClose,
  onConfirm
}: {
  selectedCount: number;
  isPending: boolean;
  onClose: () => void;
  onConfirm: (range: { targetStartAt: string; targetEndAt: string }) => void;
}) {
  const [start, setStart] = useState(DEFAULT_BACKFILL_START_INPUT);
  const [end, setEnd] = useState(DEFAULT_BACKFILL_END_INPUT);
  const canSubmit = selectedCount > 0 && start.length > 0 && end.length > 0 && start < end;
  return (
    <div className="modal-backdrop">
      <section className="backfill-dialog" role="dialog" aria-label="백필 계획 생성" aria-modal="true">
        <button className="icon-button close-button" type="button" aria-label="닫기" onClick={onClose}>
          <X size={18} />
        </button>
        <div className="panel-heading">
          <h2>백필 계획 생성</h2>
          <span>선택 코인 {selectedCount}개</span>
        </div>
        <div className="backfill-form-grid">
          <label>
            <span>수집 데이터</span>
            <select defaultValue="source_candle">
              <option value="source_candle">1분 캔들(Source Candle)</option>
            </select>
          </label>
          <label>
            <span>백필 방식</span>
            <select defaultValue="safe_restart">
              <option value="safe_restart">안전 재시작(Safe Restart)</option>
            </select>
          </label>
          <label>
            <span>수집 범위 시작 · UTC</span>
            <input
              aria-label="수집 범위 시작"
              type="datetime-local"
              value={start}
              onChange={(event) => setStart(event.currentTarget.value)}
            />
          </label>
          <label>
            <span>수집 범위 종료 · UTC</span>
            <input
              aria-label="수집 범위 종료"
              type="datetime-local"
              value={end}
              onChange={(event) => setEnd(event.currentTarget.value)}
            />
          </label>
        </div>
        <p className="helper-text">
          승인 후 워커가 이미 저장된 시작 구간은 건너뛰고 첫 빈 구간부터 지속 백필합니다.
        </p>
        <div className="dialog-actions">
          <button type="button" onClick={onClose}>취소</button>
          <button
            className="primary-action"
            type="button"
            disabled={!canSubmit || isPending}
            onClick={() =>
              onConfirm({
                targetStartAt: dateTimeLocalToUtcIso(start),
                targetEndAt: dateTimeLocalToUtcIso(end)
              })
            }
          >
            확인
          </button>
        </div>
      </section>
    </div>
  );
}
