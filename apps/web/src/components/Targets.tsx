import { useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  CircleDashed,
  Info,
  ListChecks,
  PauseCircle,
  PlayCircle,
  Search,
  Settings2,
  StopCircle,
  Trash2,
  X
} from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  controlBackfillJob,
  deleteBackfillJob,
  loadCandidateUniverse,
  startBackfillJob,
  updateCollectionTargets,
  type BackfillJob,
  type CandidateUniverseEntry,
  type OperationsSnapshot
} from "../api";
import {
  dateTimeLocalToKstIso,
  formatFreshness
} from "../operationsDisplay";
import {
  canCreateBackfillPlan,
  canSaveTargets,
  filterAndSortCandidateEntries,
  initialSelectedInstrumentIds,
  toggleSelectedInstrument,
  type SortMode
} from "../targetBackfillWorkflow";
import { InstrumentName, MiniMetric } from "./common";

const EMPTY_CANDIDATE_ENTRIES: CandidateUniverseEntry[] = [];
const DEFAULT_BACKFILL_START_INPUT = "2026-01-01T00:00";
const DEFAULT_BACKFILL_END_INPUT = "2026-02-01T00:00";

export function Targets({ snapshot }: { snapshot: OperationsSnapshot }) {
  const queryClient = useQueryClient();
  const [isBackfillDialogOpen, setBackfillDialogOpen] = useState(false);
  const [startedJobId, setStartedJobId] = useState<number | null>(null);
  const [searchText, setSearchText] = useState("");
  const [sortMode, setSortMode] = useState<SortMode>("trade");
  const universeQuery = useQuery({
    queryKey: ["candidate-universe"],
    queryFn: loadCandidateUniverse
  });
  const entries = universeQuery.data ?? EMPTY_CANDIDATE_ENTRIES;
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
  const startJobMutation = useMutation({
    mutationFn: (options: { targetStartAt: string; targetEndAt: string }) =>
      startBackfillJob(Array.from(selectedIds), options),
    onSuccess: (job) => {
      setStartedJobId(job.id);
      setBackfillDialogOpen(false);
      void queryClient.invalidateQueries({ queryKey: ["operations"] });
    }
  });
  const pauseJobMutation = useMutation({
    mutationFn: (jobId: number) => controlBackfillJob(jobId, "pause"),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["operations"] });
    }
  });
  const resumeJobMutation = useMutation({
    mutationFn: (jobId: number) => controlBackfillJob(jobId, "resume"),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["operations"] });
    }
  });
  const stopJobMutation = useMutation({
    mutationFn: (jobId: number) => controlBackfillJob(jobId, "stop"),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["operations"] });
    }
  });
  const deleteJobMutation = useMutation({
    mutationFn: (jobId: number) => deleteBackfillJob(jobId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["operations"] });
    }
  });
  const selected = selectedIds.size;
  const canSave = canSaveTargets(selected, mutation.isPending);
  const canCreatePlan =
    canCreateBackfillPlan(selected, startJobMutation.isPending);
  const backfillJobs = useMemo(
    () => snapshot.backfillJobs.filter((job) => job.status !== "planned"),
    [snapshot.backfillJobs]
  );
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
        <div className="target-table">
          <div className="target-table-head">
            <span>활성</span>
            <span>후보</span>
            <span>24시간 거래대금</span>
            <span>수집 시작일</span>
            <span>수집 최종일</span>
          </div>
          {entries.length === 0 ? <p className="helper-text">후보 유니버스를 불러오는 중입니다.</p> : null}
          {entries.length > 0 && visibleEntries.length === 0 ? (
            <p className="helper-text">검색 조건에 맞는 후보가 없습니다.</p>
          ) : null}
          {visibleEntries.slice(0, 100).map((entry) => (
            <label className="target-row" key={entry.instrument.id}>
              <span>
                <input
                  aria-label={`${entry.instrument.baseAsset} 활성 수집 대상`}
                  type="checkbox"
                  checked={selectedIds.has(entry.instrument.id)}
                  onChange={() => toggle(entry.instrument.id)}
                />
              </span>
              <InstrumentName instrument={entry.instrument} />
              <strong>{entry.accTradePrice24hDisplay}</strong>
              <span>{formatCollectionBoundary(entry.collectedStartAt)}</span>
              <span className="collection-end-cell">
                {formatCollectionBoundary(entry.collectedEndAt)}
                {entry.isRealtimeTarget ? (
                  <em className="realtime-target-badge">실시간</em>
                ) : null}
              </span>
            </label>
          ))}
        </div>
      </section>
      <section className="panel side-panel">
        <div className="panel-heading">
          <h2>백필 작업 패널</h2>
          <Settings2 size={18} />
        </div>
        <MiniMetric
          label="선택 코인"
          value={`${selected.toLocaleString("ko-KR")}개`}
          detail="백필 시작 대상"
        />
        <MiniMetric
          label="작업 상태"
          value={`${backfillJobs.length.toLocaleString("ko-KR")}건`}
          detail="pending/running 포함"
        />
        <MiniMetric
          label="감사 로그"
          value={`대상 변경 ${snapshot.dashboard.auditLogSummary.targetChangeCount24h}건`}
          detail={`${snapshot.dashboard.auditLogSummary.latestChangeLabel} · 최근 24시간`}
        />
        {startedJobId !== null ? (
          <p className="success-text">백필 작업 {startedJobId} 시작됨</p>
        ) : null}
        {startJobMutation.isError ? <p className="error-text">백필 시작에 실패했습니다.</p> : null}
        <BackfillJobs
          jobs={backfillJobs}
          pendingPauseJobId={pauseJobMutation.variables}
          pendingResumeJobId={resumeJobMutation.variables}
          pendingStopJobId={stopJobMutation.variables}
          pendingDeleteJobId={deleteJobMutation.variables}
          onPause={(jobId) => pauseJobMutation.mutate(jobId)}
          onResume={(jobId) => resumeJobMutation.mutate(jobId)}
          onStop={(jobId) => stopJobMutation.mutate(jobId)}
          onDelete={(jobId) => deleteJobMutation.mutate(jobId)}
        />
      </section>
      {isBackfillDialogOpen ? (
        <BackfillPlanDialog
          selectedCount={selected}
          isPending={startJobMutation.isPending}
          onClose={() => setBackfillDialogOpen(false)}
          onConfirm={(range) => startJobMutation.mutate(range)}
        />
      ) : null}
    </section>
  );
}

function BackfillJobs({
  jobs,
  pendingPauseJobId,
  pendingResumeJobId,
  pendingStopJobId,
  pendingDeleteJobId,
  onPause,
  onResume,
  onStop,
  onDelete
}: {
  jobs: BackfillJob[];
  pendingPauseJobId: number | undefined;
  pendingResumeJobId: number | undefined;
  pendingStopJobId: number | undefined;
  pendingDeleteJobId: number | undefined;
  onPause: (jobId: number) => void;
  onResume: (jobId: number) => void;
  onStop: (jobId: number) => void;
  onDelete: (jobId: number) => void;
}) {
  return (
    <section className="approved-backfill-panel" aria-label="백필 작업 목록">
      <div className="subheading">
        <h2>백필 작업</h2>
        <span>{jobs.length.toLocaleString("ko-KR")}건</span>
      </div>
      {jobs.length === 0 ? (
        <p className="helper-text">백필 작업이 없습니다.</p>
      ) : null}
      <div className="approved-backfill-list">
        {jobs.slice(0, 8).map((job) => (
          <article className="approved-backfill-card" key={job.id}>
            <div className="approved-backfill-title">
              <strong>작업 {job.id}</strong>
              <span className={`backfill-job-status ${job.status}`}>
                <BackfillJobStatusIcon status={job.status} />
                {backfillJobStatusLabel(job.status)}
              </span>
            </div>
            <div className="approved-backfill-meta">
              <span>{backfillDataTypeLabel(job.dataType)}</span>
              <span>{formatFreshness(job.createdAt)}</span>
            </div>
            <div className="approved-backfill-detail">
              <span className="backfill-target-summary">
                <span title={backfillJobTargetTooltip(job)}>{backfillJobTargetSummary(job)}</span>
                {hasBackfillJobTargetTooltip(job) ? (
                  <span
                    className="inline-info-icon"
                    role="img"
                    aria-label={`작업 ${job.id} 대상 전체 보기`}
                    title={backfillJobTargetTooltip(job)}
                  >
                    <Info size={13} />
                  </span>
                ) : null}
              </span>
              <span>
                {formatBackfillJobRange(job.targetStartAt, job.targetEndAt)}
              </span>
            </div>
            <div
              className="approved-backfill-progress"
              aria-label={`작업 ${job.id} 진행률 ${job.progressPercent}%`}
            >
              <span style={{ width: `${backfillProgressWidth(job.progressPercent)}%` }} />
            </div>
            <div className="approved-backfill-footer">
              <span>진행률</span>
              <strong>{job.progressPercent}%</strong>
            </div>
            <div className="approved-backfill-live-metrics" aria-label={`작업 ${job.id} 진행 상세`}>
              <span>대상 {backfillTargetProgressLabel(job)}</span>
              <span>완료 {job.completedTargetCount.toLocaleString("ko-KR")}개</span>
              <span>현재 {job.currentTarget?.baseAsset ?? "-"}</span>
              <span>
                백필 row {job.currentTargetBackfillRowCount.toLocaleString("ko-KR")}
              </span>
              <span>
                결측 구간 처리 {job.processedMissingRangeCount.toLocaleString("ko-KR")}/
                {job.estimatedMissingRangeCount.toLocaleString("ko-KR")}
              </span>
              <span>예상 요청 {job.estimatedRequestCount.toLocaleString("ko-KR")}</span>
            </div>
            <div className="approved-backfill-actions">
              {canResumeBackfillJob(job) ? (
                <button
                  type="button"
                  aria-label={`작업 ${job.id} 재개`}
                  disabled={pendingResumeJobId === job.id}
                  onClick={() => onResume(job.id)}
                >
                  <PlayCircle size={14} />
                  재개
                </button>
              ) : (
                <button
                  type="button"
                  aria-label={`작업 ${job.id} 멈춤`}
                  disabled={!canPauseBackfillJob(job) || pendingPauseJobId === job.id}
                  onClick={() => onPause(job.id)}
                >
                  <PauseCircle size={14} />
                  멈춤(Pause)
                </button>
              )}
              <button
                type="button"
                aria-label={`작업 ${job.id} 중지`}
                disabled={!canStopBackfillJob(job) || pendingStopJobId === job.id}
                onClick={() => onStop(job.id)}
              >
                <StopCircle size={14} />
                중지
              </button>
              <button
                type="button"
                aria-label={`작업 ${job.id} 삭제`}
                disabled={!canDeleteBackfillJob(job) || pendingDeleteJobId === job.id}
                onClick={() => onDelete(job.id)}
              >
                <Trash2 size={14} />
                삭제
              </button>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

function BackfillJobStatusIcon({ status }: { status: BackfillJob["status"] }) {
  if (status === "running") return <CircleDashed size={14} />;
  if (status === "paused") return <PauseCircle size={14} />;
  if (status === "stopped") return <StopCircle size={14} />;
  if (status === "succeeded") return <CheckCircle2 size={14} />;
  if (status === "failed") return <AlertCircle size={14} />;
  return <ListChecks size={14} />;
}

function backfillJobStatusLabel(status: BackfillJob["status"]): string {
  const labels: Record<BackfillJob["status"], string> = {
    planned: "계획됨",
    pending: "대기 중",
    running: "실행 중",
    paused: "일시정지",
    stopped: "중지",
    succeeded: "완료",
    failed: "실패"
  };
  return labels[status];
}

function backfillDataTypeLabel(dataType: string): string {
  if (dataType === "source_candle") return "1분 캔들(Source Candle)";
  return dataType;
}

function backfillJobTargetSummary(job: BackfillJob): string {
  if (job.targets.length === 0) return "대상 없음";
  const symbols = job.targets.map((target) => target.baseAsset);
  if (symbols.length <= 4) return symbols.join(", ");
  return `${symbols.slice(0, 4).join(", ")} 외 ${(symbols.length - 4).toLocaleString("ko-KR")}개`;
}

function backfillJobTargetTooltip(job: BackfillJob): string {
  if (job.targets.length === 0) return "대상 없음";
  return job.targets.map((target) => target.baseAsset).join(", ");
}

function hasBackfillJobTargetTooltip(job: BackfillJob): boolean {
  return job.targets.length > 4;
}

function backfillTargetProgressLabel(job: BackfillJob): string {
  const current =
    job.runningTargetIndex ?? Math.min(job.completedTargetCount + 1, job.totalTargetCount);
  return `${current.toLocaleString("ko-KR")}/${job.totalTargetCount.toLocaleString("ko-KR")}`;
}

function formatBackfillJobRange(startAt: string, endAt: string): string {
  return `${formatKstDateTimeMinute(startAt)} ~ ${formatKstDateTimeMinute(endAt)}`;
}

function formatKstDateTimeMinute(value: string): string {
  const date = new Date(value);
  const parts = new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23"
  }).formatToParts(date);
  const part = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((item) => item.type === type)?.value ?? "";
  return `${part("year")}년 ${part("month")}월 ${part("day")}일 ${part("hour")}:${part(
    "minute"
  )}`;
}

function canStopBackfillJob(job: BackfillJob): boolean {
  return job.status === "pending" || job.status === "running" || job.status === "paused";
}

function canPauseBackfillJob(job: BackfillJob): boolean {
  return job.status === "pending" || job.status === "running";
}

function canResumeBackfillJob(job: BackfillJob): boolean {
  return job.status === "paused" || job.status === "failed";
}

function canDeleteBackfillJob(job: BackfillJob): boolean {
  return job.status !== "running";
}

function backfillProgressWidth(value: string): number {
  const percent = Number(value);
  if (!Number.isFinite(percent)) return 0;
  return Math.min(100, Math.max(0, percent));
}

function formatCollectionBoundary(value: string | null): string {
  if (value === null) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  const parts = new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23"
  }).formatToParts(date);
  const part = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((item) => item.type === type)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")} ${part("hour")}:${part("minute")} KST`;
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
            <span>수집 범위 시작 · KST</span>
            <input
              aria-label="수집 범위 시작"
              type="datetime-local"
              value={start}
              onChange={(event) => setStart(event.currentTarget.value)}
            />
          </label>
          <label>
            <span>수집 범위 종료 · KST</span>
            <input
              aria-label="수집 범위 종료"
              type="datetime-local"
              value={end}
              onChange={(event) => setEnd(event.currentTarget.value)}
            />
          </label>
        </div>
        <p className="helper-text">
          백필 시작 후 워커가 이미 저장된 시작 구간은 건너뛰고 첫 빈 구간부터 지속 백필합니다.
        </p>
        <div className="dialog-actions">
          <button type="button" onClick={onClose}>취소</button>
          <button
            className="primary-action"
            type="button"
            disabled={!canSubmit || isPending}
            onClick={() =>
              onConfirm({
                targetStartAt: dateTimeLocalToKstIso(start),
                targetEndAt: dateTimeLocalToKstIso(end)
              })
            }
          >
            백필 시작
          </button>
        </div>
      </section>
    </div>
  );
}
