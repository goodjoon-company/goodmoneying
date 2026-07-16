import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Ban, CheckCircle2, CircleDashed, PauseCircle, PlayCircle, SlidersHorizontal, X } from "lucide-react";
import {
  loadDataFoundation,
  updateMarketTargetState,
  type CollectionPolicyDataType,
  type CoverageCounts,
  type CoverageIntervalStatus,
  type DataFoundationMarket,
  type MarketCollectionPolicy
} from "../api";

const COVERAGE_STATES: {
  status: CoverageIntervalStatus;
  label: string;
  description: string;
}[] = [
  { status: "available", label: "사용 가능", description: "원천 행과 manifest checksum이 있음" },
  {
    status: "no_trade",
    label: "무거래 확인",
    description: "동일 성공 페이지의 양쪽 인접 캔들로 내부 무체결을 확인"
  },
  { status: "missing", label: "복구 필요", description: "수집이 기대됐으나 최종 성공 증거가 없음" },
  { status: "unavailable", label: "획득 불가", description: "상장 전·종료 후 또는 공식 보존 범위 밖" },
  { status: "unverified", label: "미검증", description: "아직 요청 성공 여부나 의미를 확정하지 못함" }
];

export function CoverageQuality() {
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState("");
  const [editingMarket, setEditingMarket] = useState<DataFoundationMarket | null>(null);
  const query = useQuery({
    queryKey: ["data-foundation"],
    queryFn: loadDataFoundation,
    refetchInterval: 15_000
  });
  const mutation = useMutation({
    mutationFn: ({
      marketCode,
      state,
      label,
      policy
    }: {
      marketCode: string;
      state: "active" | "paused" | "excluded";
      label: string;
      policy?: MarketCollectionPolicy;
    }) =>
      updateMarketTargetState(
        marketCode,
        state,
        `Coverage & Quality 화면에서 ${label}`,
        policy
      ),
    onSuccess: (_response, variables) => {
      setNotice(`${variables.marketCode} ${variables.label} 요청을 저장했습니다.`);
      if (variables.policy) setEditingMarket(null);
      void queryClient.invalidateQueries({ queryKey: ["data-foundation"] });
    }
  });
  const krwMarkets = useMemo(
    () => query.data?.markets.filter((market) => market.quoteCurrency === "KRW") ?? [],
    [query.data]
  );

  if (query.isPending) {
    return <section className="coverage-quality loading-state">커버리지 계약을 불러오는 중</section>;
  }
  if (query.isError || !query.data) {
    return <section className="coverage-quality error-state">커버리지 계약을 불러오지 못했습니다.</section>;
  }

  return (
    <section className="coverage-quality" aria-labelledby="coverage-quality-title">
      <header className="coverage-quality-header">
        <div>
          <p className="eyebrow">P1 · 데이터 기반</p>
          <h2 id="coverage-quality-title">Coverage &amp; Quality</h2>
          <p>
            모든 KRW 시장을 <strong>{formatUtcPolicyStart(query.data.policyStartAt)}</strong>
            부터 자동 백필하고 실시간 수집을 계속합니다.
          </p>
        </div>
        <dl className="coverage-policy-summary" aria-label="KRW 기본 정책 요약">
          <SummaryMetric label="공식 KRW 시장" value={query.data.summary.krwMarketCount} />
          <SummaryMetric label="활성 데이터 대상" value={query.data.summary.activeTargetCount} />
          <SummaryMetric label="자동 백필 대기" value={query.data.summary.pendingBackfillJobCount} />
          <SummaryMetric label="실시간 desired" value={query.data.summary.desiredSubscriptionCount} />
        </dl>
      </header>

      <section className="coverage-state-grid" aria-label="5단계 커버리지 상태">
        {COVERAGE_STATES.map((item) => (
          <article className={`coverage-state-card status-${item.status}`} key={item.status}>
            <CoverageIcon status={item.status} />
            <div>
              <strong>{item.status} · {item.label}</strong>
              <p>{item.description}</p>
            </div>
            <span>{query.data.summary.coverageCounts[item.status].toLocaleString("ko-KR")}</span>
          </article>
        ))}
      </section>

      {notice ? <p className="coverage-notice" role="status" aria-live="polite">{notice}</p> : null}
      {mutation.isError ? <p className="error-text" role="alert">수집 정책 변경에 실패했습니다.</p> : null}

      <section className="panel coverage-market-panel">
        <div className="panel-heading">
          <h3>KRW 시장 정책과 품질</h3>
          <span>{krwMarkets.length.toLocaleString("ko-KR")}개 · 자동 편입</span>
        </div>
        <div className="coverage-table-wrap">
          <table className="coverage-market-table">
            <thead>
              <tr>
                <th scope="col">시장</th>
                <th scope="col">공식 상태</th>
                <th scope="col">정책</th>
                <th scope="col">데이터 유형</th>
                <th scope="col">커버리지</th>
                <th scope="col">작업</th>
              </tr>
            </thead>
            <tbody>
              {krwMarkets.map((market) => (
                <MarketRow
                  key={market.marketCode}
                  market={market}
                  pending={mutation.isPending && mutation.variables?.marketCode === market.marketCode}
                  onChange={(state, label) => mutation.mutate({ marketCode: market.marketCode, state, label })}
                  onEdit={() => setEditingMarket(market)}
                />
              ))}
            </tbody>
          </table>
        </div>
      </section>
      {editingMarket?.collectionPolicy ? (
        <PolicyEditor
          key={editingMarket.marketCode}
          market={editingMarket}
          pending={mutation.isPending}
          onClose={() => setEditingMarket(null)}
          onSave={(policy) => mutation.mutate({
            marketCode: editingMarket.marketCode,
            state: editingMarket.targetStatus === "not_targeted" ? "paused" : editingMarket.targetStatus,
            label: "정책 저장",
            policy
          })}
        />
      ) : null}
    </section>
  );
}

function SummaryMetric({ label, value }: { label: string; value: number }) {
  return <div><dt>{label}</dt><dd>{value.toLocaleString("ko-KR")}</dd></div>;
}

function MarketRow({
  market,
  pending,
  onChange,
  onEdit
}: {
  market: DataFoundationMarket;
  pending: boolean;
  onChange: (state: "active" | "paused" | "excluded", label: string) => void;
  onEdit: () => void;
}) {
  const policyLabel = market.targetStatus === "active" ? "활성" : market.targetStatus === "paused" ? "일시정지" : "제외";
  return (
    <tr>
      <th scope="row"><strong>{market.marketCode}</strong><span>{market.koreanName}</span></th>
      <td>{market.tradingStatus === "active" ? "거래 지원" : "거래 중단"}{market.marketWarning !== "NONE" ? ` · ${market.marketWarning}` : ""}</td>
      <td><span className={`policy-state policy-${market.targetStatus}`}>{policyLabel}</span></td>
      <td>{market.activeDataTypeCount}/{market.totalDataTypeCount}</td>
      <td><CoverageCountList counts={market.coverageCounts} /></td>
      <td>
        <div className="coverage-row-actions">
          <button type="button" disabled={pending || !market.collectionPolicy} aria-label={`${market.marketCode} 정책 편집`} onClick={onEdit}><SlidersHorizontal size={15} />정책</button>
          {market.targetStatus === "active" ? (
            <>
              <button type="button" disabled={pending} aria-label={`${market.marketCode} 일시정지`} onClick={() => onChange("paused", "일시정지")}><PauseCircle size={15} />일시정지</button>
              <button type="button" disabled={pending} aria-label={`${market.marketCode} 제외`} onClick={() => onChange("excluded", "제외")}><Ban size={15} />제외</button>
            </>
          ) : (
            <button type="button" disabled={pending || market.tradingStatus !== "active"} aria-label={`${market.marketCode} 재개`} onClick={() => onChange("active", "재개")}><PlayCircle size={15} />재개</button>
          )}
        </div>
      </td>
    </tr>
  );
}

const POLICY_DATA_TYPES: Array<{
  value: CollectionPolicyDataType;
  label: string;
}> = [
  { value: "source_candle", label: "원천 캔들" },
  { value: "trade_event", label: "실시간 체결" },
  { value: "orderbook_snapshot", label: "호가 스냅숏" },
  { value: "ticker_snapshot", label: "티커 스냅숏" }
];

function PolicyEditor({
  market,
  pending,
  onClose,
  onSave
}: {
  market: DataFoundationMarket;
  pending: boolean;
  onClose: () => void;
  onSave: (policy: MarketCollectionPolicy) => void;
}) {
  const policy = market.collectionPolicy!;
  const [startAt, setStartAt] = useState(toUtcLocalInput(policy.startAt));
  const [dataTypes, setDataTypes] = useState<CollectionPolicyDataType[]>(policy.dataTypes);
  const [retentionDays, setRetentionDays] = useState(policy.retentionDays?.toString() ?? "");
  const [priority, setPriority] = useState(policy.priority.toString());
  const [continuous, setContinuous] = useState(policy.continuous);
  const [error, setError] = useState("");

  const submit = () => {
    if (dataTypes.length === 0) {
      setError("수집 데이터 유형을 하나 이상 선택해야 합니다.");
      return;
    }
    onSave({
      startAt: new Date(`${startAt}:00Z`).toISOString(),
      dataTypes,
      candleUnit: "1m",
      retentionDays: retentionDays ? Number(retentionDays) : null,
      priority: Number(priority),
      continuous
    });
  };

  return (
    <div className="coverage-policy-modal" role="dialog" aria-modal="true" aria-labelledby="coverage-policy-title">
      <div className="coverage-policy-editor">
        <header>
          <div><p className="eyebrow">시장별 자동 수집 정책</p><h3 id="coverage-policy-title">{market.marketCode} 정책 편집</h3></div>
          <button type="button" aria-label="정책 편집 닫기" onClick={onClose}><X aria-hidden="true" size={18} /></button>
        </header>
        <div className="coverage-policy-fields">
          <label>수집 시작 UTC<input aria-label="수집 시작 UTC" type="datetime-local" value={startAt} onChange={(event) => setStartAt(event.target.value)} required /></label>
          <fieldset><legend>수집 데이터 유형</legend>{POLICY_DATA_TYPES.map((item) => <label key={item.value}><input type="checkbox" checked={dataTypes.includes(item.value)} onChange={(event) => setDataTypes((current) => event.target.checked ? [...current, item.value] : current.filter((value) => value !== item.value))} />{item.label}</label>)}</fieldset>
          <label>기준 주기<select aria-label="기준 주기" value="1m" disabled><option value="1m">1분</option></select></label>
          <label>보존 기간 일수<input aria-label="보존 기간 일수" type="number" min="1" max="36500" value={retentionDays} placeholder="무기한" onChange={(event) => setRetentionDays(event.target.value)} /></label>
          <label>우선순위<input aria-label="우선순위" type="number" min="1" max="1000" value={priority} onChange={(event) => setPriority(event.target.value)} required /></label>
          <label className="coverage-policy-continuous"><input type="checkbox" checked={continuous} onChange={(event) => setContinuous(event.target.checked)} />신규 데이터 지속 수집</label>
        </div>
        {error ? <p className="error-text" role="alert">{error}</p> : null}
        <footer><button type="button" onClick={onClose}>취소</button><button type="button" disabled={pending || !startAt || !priority} aria-label={`${market.marketCode} 정책 저장`} onClick={submit}>정책 저장</button></footer>
      </div>
    </div>
  );
}

function toUtcLocalInput(value: string): string {
  return new Date(value).toISOString().slice(0, 16);
}

function CoverageCountList({ counts }: { counts: CoverageCounts }) {
  return <ul className="coverage-count-list" aria-label="커버리지 상태별 구간 수">{COVERAGE_STATES.map(({ status, label }) => <li key={status}><span className={`coverage-dot status-${status}`} />{label} {counts[status]}</li>)}</ul>;
}

function CoverageIcon({ status }: { status: CoverageIntervalStatus }) {
  if (status === "available") return <CheckCircle2 aria-hidden="true" />;
  if (status === "missing") return <AlertTriangle aria-hidden="true" />;
  if (status === "unavailable") return <Ban aria-hidden="true" />;
  return <CircleDashed aria-hidden="true" />;
}

function formatUtcPolicyStart(value: string): string {
  const date = new Date(value);
  const year = date.getUTCFullYear();
  const month = String(date.getUTCMonth() + 1).padStart(2, "0");
  const day = String(date.getUTCDate()).padStart(2, "0");
  const hour = String(date.getUTCHours()).padStart(2, "0");
  const minute = String(date.getUTCMinutes()).padStart(2, "0");
  return `${year}-${month}-${day} ${hour}:${minute} UTC`;
}
