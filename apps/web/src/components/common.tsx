import { CheckCircle2, CircleAlert } from "lucide-react";
import {
  type Candle,
  type CollectionDashboardTarget,
  type CoverageSegment,
  type Instrument,
  type Status
} from "../api";
import { formatFreshness } from "../operationsDisplay";

export function CoverageBar({ segments }: { segments: CoverageSegment[] }) {
  return (
    <div className="coverage-bar" aria-label="구간형 진행 상태">
      {segments.map((segment, index) => (
        <span
          className={`coverage-segment ${segment.status}`}
          key={`${segment.dataType}-${segment.status}-${index}`}
          title={segment.label}
          style={{ left: `${segment.offsetPercent}%`, width: `${segment.widthPercent}%` }}
        />
      ))}
    </div>
  );
}

export function CoverageMeter({ value }: { value: string }) {
  const numeric = Math.max(0, Math.min(100, Number(value)));
  return (
    <span className="coverage-meter">
      <span style={{ width: `${numeric}%` }} />
      <em>{numeric.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}%</em>
    </span>
  );
}

export function CandleCountMeter({
  count,
  progress,
  segments
}: {
  count: number;
  progress: string;
  segments: CoverageSegment[];
}) {
  return (
    <span className="candle-count-meter" aria-label="저장된 가격 분봉 수">
      <CoverageBar segments={segments} />
      <strong>{count.toLocaleString("ko-KR")}</strong>
      <em>{Number(progress).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}%</em>
    </span>
  );
}

export function StatusIcon({ status }: { status: Status }) {
  if (status === "normal") {
    return <CheckCircle2 className="health-icon normal" size={18} aria-hidden="true" />;
  }
  if (status === "warning") {
    return <CircleAlert className="health-icon warning" size={18} aria-hidden="true" />;
  }
  return <CircleAlert className="health-icon incident" size={18} aria-hidden="true" />;
}

export function StatusDot({ status }: { status: Status }) {
  return <span className={`status-dot ${status}`} aria-hidden="true" />;
}

export function InstrumentName({ instrument }: { instrument: Instrument }) {
  return (
    <span className="instrument-name">
      <strong>{instrument.baseAsset} / {instrument.quoteCurrency}</strong>
      <em>{instrument.displayName}</em>
    </span>
  );
}

export function InstrumentTitle({ instrument }: { instrument: Instrument }) {
  return <>{instrument.baseAsset} / {instrument.quoteCurrency}</>;
}

export function TimeInline({ value, zone }: { value: string; zone: "KST" }) {
  return (
    <span className="time-inline">
      {value}
      <em>{zone}</em>
    </span>
  );
}

export function Metric({
  label,
  value,
  hint,
  tone = "default"
}: {
  label: string;
  value: string;
  hint: string;
  tone?: "default" | "warning" | "danger";
}) {
  return (
    <article className={`metric ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      <em>{hint}</em>
    </article>
  );
}

export function MiniMetric({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <article className="mini-metric">
      <span>{label}</span>
      <strong>{value}</strong>
      <em>{detail}</em>
    </article>
  );
}

export function statusText(status: string) {
  if (status === "normal") return "정상";
  if (status === "warning") return "주의";
  if (status === "incident") return "장애";
  return status;
}

export function statusFromTarget(target: CollectionDashboardTarget): Status {
  if (target.overallStatus === "incident") return "incident";
  if (target.overallStatus === "warning") return "warning";
  return "normal";
}

export function formatBackfillRange(target: CollectionDashboardTarget) {
  const candleStatus = target.dataStatuses.find((status) => status.dataType === "source_candle");
  return `${formatFreshness(target.plan.rangeStartAt)} ~ ${formatFreshness(
    candleStatus?.lastSuccessfulAt ?? target.plan.rangeStartAt
  )}`;
}

export function sampleCandles(candles: Candle[], maxCount: number) {
  if (candles.length <= maxCount) return candles;
  const step = candles.length / maxCount;
  return Array.from({ length: maxCount }, (_, index) => candles[Math.floor(index * step)]).filter(
    Boolean
  );
}
