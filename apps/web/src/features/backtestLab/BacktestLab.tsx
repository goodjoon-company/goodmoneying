import { useEffect, useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { RefreshCcw } from "lucide-react";
import { loadBacktestRun, loadBacktestRuns, type BacktestRun, type BacktestRunSummary } from "../../api";
import { formatKstDateTime } from "../../displayFormat";

export function BacktestLab() {
  const [runIdInput, setRunIdInput] = useState("");
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const runsQuery = useInfiniteQuery({
    queryKey: ["backtest-runs", 25],
    queryFn: ({ pageParam }) => loadBacktestRuns({ pageSize: 25, cursor: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.nextCursor
  });
  const runs = runsQuery.data?.pages.flatMap((page) => page.items) ?? [];

  useEffect(() => {
    if (selectedRunId === null && runs.length > 0) {
      const firstRunId = runs[0].backtestRunId;
      setSelectedRunId(firstRunId);
      setRunIdInput(String(firstRunId));
    }
  }, [runs, selectedRunId]);

  const runQuery = useQuery({
    queryKey: ["backtest-run", selectedRunId],
    queryFn: () => {
      if (selectedRunId === null) throw new Error("백테스트 run이 선택되지 않았다.");
      return loadBacktestRun(selectedRunId);
    },
    enabled: selectedRunId !== null
  });
  const run = runQuery.data ?? null;

  return (
    <section className="backtest-lab" aria-labelledby="backtest-lab-title">
      <header className="backtest-lab-title-row">
        <div>
          <p className="eyebrow">P4-4 · Backtest Store 목록과 조회</p>
          <h2 id="backtest-lab-title">Backtest Lab</h2>
          <p>저장된 백테스트 run 목록에서 결과를 발견하고 상세 성과, 체결, 산출물을 읽기 전용으로 확인합니다.</p>
        </div>
        <button
          type="button"
          aria-label="Backtest Lab 새로고침"
          onClick={() => {
            void runsQuery.refetch();
            if (selectedRunId !== null) void runQuery.refetch();
          }}
        >
          <RefreshCcw size={16} />새로고침
        </button>
      </header>

      <form
        className="backtest-lab-form"
        aria-label="백테스트 run 조회"
        onSubmit={(event) => {
          event.preventDefault();
          const nextRunId = Number(runIdInput);
          if (Number.isInteger(nextRunId) && nextRunId > 0) {
            setSelectedRunId(nextRunId);
          }
        }}
      >
        <label>
          백테스트 Run ID
          <input
            min="1"
            type="number"
            value={runIdInput}
            onChange={(event) => setRunIdInput(event.target.value)}
          />
        </label>
        <button type="submit">Run 조회</button>
      </form>

      <BacktestRunList
        isLoading={runsQuery.isLoading}
        items={runs}
        hasMore={runsQuery.hasNextPage}
        loadingMore={runsQuery.isFetchingNextPage}
        selectedRunId={selectedRunId}
        onLoadMore={() => void runsQuery.fetchNextPage()}
        onSelect={(backtestRunId) => {
          setSelectedRunId(backtestRunId);
          setRunIdInput(String(backtestRunId));
        }}
      />

      {runQuery.error ? (
        <div role="alert" aria-label="백테스트 run 조회 오류" className="backtest-lab-alert">
          백테스트 run을 불러오지 못했습니다.
        </div>
      ) : null}
      {!run && runQuery.isLoading ? <p>백테스트 run을 불러오는 중</p> : null}
      {run ? <BacktestRunPanel run={run} /> : null}
    </section>
  );
}

function BacktestRunList({
  isLoading,
  items,
  hasMore,
  loadingMore,
  selectedRunId,
  onLoadMore,
  onSelect
}: {
  isLoading: boolean;
  items: BacktestRunSummary[];
  hasMore: boolean;
  loadingMore: boolean;
  selectedRunId: number | null;
  onLoadMore: () => void;
  onSelect: (backtestRunId: number) => void;
}) {
  return (
    <section className="backtest-lab-panel backtest-lab-wide" aria-label="저장된 백테스트 run 목록">
      <div className="panel-heading">
        <h3>저장된 run 목록</h3>
        <span>{hasMore ? "다음 페이지 있음" : "현재 페이지"}</span>
      </div>
      {isLoading ? <p>백테스트 run 목록을 불러오는 중</p> : null}
      {!isLoading && items.length === 0 ? <p>저장된 백테스트 run이 없습니다.</p> : null}
      <ul className="backtest-lab-run-list">
        {items.map((item) => (
          <li key={item.backtestRunId}>
            <button
              type="button"
              aria-label={`Run #${item.backtestRunId} 선택`}
              aria-pressed={selectedRunId === item.backtestRunId}
              onClick={() => onSelect(item.backtestRunId)}
            >
              <strong>Run #{item.backtestRunId} 선택</strong>
              <span>{item.status}</span>
              <small>
                전략 #{item.strategyVersionId} · 데이터셋 #{item.datasetVersionId} · {item.engineVersion}
              </small>
            </button>
          </li>
        ))}
      </ul>
      {hasMore ? (
        <button
          type="button"
          className="backtest-lab-load-more"
          disabled={loadingMore}
          onClick={onLoadMore}
        >
          {loadingMore ? "다음 run 목록을 불러오는 중" : "다음 run 목록 불러오기"}
        </button>
      ) : null}
    </section>
  );
}

function BacktestRunPanel({ run }: { run: BacktestRun }) {
  const finalEquity = run.metrics.find((metric) => metric.metricName === "finalEquity");
  return (
    <div className="backtest-lab-grid">
      <section className="backtest-lab-panel" aria-label="백테스트 run 요약">
        <div className="panel-heading">
          <h3>Run #{run.backtestRunId}</h3>
          <span className="backtest-lab-status">{run.status}</span>
        </div>
        <dl className="backtest-lab-summary">
          <div>
            <dt>전략 version</dt>
            <dd>#{run.strategyVersionId}</dd>
          </div>
          <div>
            <dt>데이터셋 version</dt>
            <dd>#{run.datasetVersionId}</dd>
          </div>
          <div>
            <dt>최종 자본(finalEquity)</dt>
            <dd>{finalEquity?.metricValue ?? "-"}</dd>
          </div>
          <div>
            <dt>입력 hash</dt>
            <dd>{run.inputHash}</dd>
          </div>
          <div>
            <dt>결과 hash</dt>
            <dd>{run.resultHash}</dd>
          </div>
        </dl>
      </section>

      <section className="backtest-lab-panel" aria-label="백테스트 지표">
        <div className="panel-heading">
          <h3>성과 지표</h3>
        </div>
        <ul className="backtest-lab-metrics">
          {run.metrics.map((metric) => (
            <li key={`${metric.metricName}-${metric.scopeKey}`}>
              <span>{metric.metricName}</span>
              <strong>{metric.metricValue}</strong>
              <small>{metric.scopeKey}</small>
            </li>
          ))}
        </ul>
      </section>

      <section className="backtest-lab-panel backtest-lab-wide" aria-label="백테스트 체결">
        <div className="panel-heading">
          <h3>체결 결과</h3>
        </div>
        <div className="backtest-lab-table-wrap">
          <table aria-label="백테스트 체결 결과" className="backtest-lab-table">
            <thead>
              <tr>
                <th>순번</th>
                <th>방향</th>
                <th>상태</th>
                <th>요청/체결/잔량</th>
                <th>체결가</th>
                <th>수수료</th>
                <th>발생 KST</th>
              </tr>
            </thead>
            <tbody>
              {run.trades.map((trade) => (
                <tr key={trade.tradeSequence}>
                  <td>{trade.tradeSequence}</td>
                  <td>{trade.side}</td>
                  <td>{trade.status}</td>
                  <td>{trade.requestedQuantity} / {trade.filledQuantity} / {trade.remainingQuantity}</td>
                  <td>{trade.fillPrice}</td>
                  <td>{trade.feePaid}</td>
                  <td>{formatKstDateTime(trade.occurredAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="backtest-lab-panel backtest-lab-wide" aria-label="백테스트 산출물">
        <div className="panel-heading">
          <h3>산출물</h3>
        </div>
        <ul className="backtest-lab-artifacts">
          {run.artifacts.map((artifact) => (
            <li key={`${artifact.artifactType}-${artifact.contentHash}`}>
              <strong>{artifact.artifactType}</strong>
              <span>{artifact.mediaType}</span>
              <small>{artifact.contentHash}</small>
              {artifact.storageUri ? <code>{artifact.storageUri}</code> : null}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
