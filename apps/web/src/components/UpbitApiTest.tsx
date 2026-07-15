import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { formatNumber } from "../operationsDisplay";
import {
  fetchUpbitCandles,
  fetchUpbitMarkets,
  mergeUpbitCandles,
  type UpbitCandle,
  type UpbitCandleInterval,
  type UpbitMarket
} from "../upbitApi";
import { UpbitCandleChart, type Indicator } from "./UpbitCandleChart";

const intervals: { value: UpbitCandleInterval; label: string }[] = [
  { value: "1m", label: "1분" }, { value: "3m", label: "3분" }, { value: "5m", label: "5분" },
  { value: "10m", label: "10분" }, { value: "15m", label: "15분" }, { value: "30m", label: "30분" },
  { value: "1h", label: "1시간" }, { value: "4h", label: "4시간" }, { value: "1d", label: "일봉" },
  { value: "1w", label: "주봉" }, { value: "1M", label: "월봉" }
];

type CandleResult = {
  kind: "candles";
  market: string;
  interval: UpbitCandleInterval;
  candles: UpbitCandle[];
  raw: unknown[];
  hasEndTime: boolean;
  convertingPriceUnit?: "KRW";
  convertedClose?: number;
  requestId: number;
  exhausted: Record<PageDirection, boolean>;
};

type Result =
  | { kind: "markets"; markets: UpbitMarket[]; raw: unknown[] }
  | CandleResult;

type PageDirection = "past" | "future";

let nextUpbitBrowserRequestAt = 0;

export function UpbitApiTest() {
  const [tab, setTab] = useState<"markets" | "candles">("markets");
  const [markets, setMarkets] = useState<UpbitMarket[]>([]);
  const [isDetails, setDetails] = useState(false);
  const [marketSearch, setMarketSearch] = useState("");
  const [market, setMarket] = useState("");
  const [interval, setInterval] = useState<UpbitCandleInterval>("1d");
  const [count, setCount] = useState("100");
  const [endTime, setEndTime] = useState("");
  const [convertToKrw, setConvertToKrw] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const resultRef = useRef<Result | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pageError, setPageError] = useState<string | null>(null);
  const [isLoadingMarkets, setLoadingMarkets] = useState(false);
  const [isLoadingCandles, setLoadingCandles] = useState(false);
  const [pageLoading, setPageLoading] = useState<PageDirection | null>(null);
  const initialRequestRef = useRef<AbortController | null>(null);
  const pageRequestControllersRef = useRef(new Set<AbortController>());
  const candleRequestIdRef = useRef(0);
  const pageDirectionsRef = useRef(new Set<PageDirection>());
  const [edgeRequestVersion, setEdgeRequestVersion] = useState(0);
  const { enqueue, waitingSeconds, abortAll } = useUpbitRequestQueue();

  const candles = result?.kind === "candles" ? result.candles : [];
  const indicators = useMemo(() => calculateIndicators(candles), [candles]);
  const latest = candles.at(-1);
  const latestIndicator = indicators.at(-1);
  const visibleMarkets = markets.filter((item) => `${item.market} ${item.koreanName} ${item.englishName}`.toLowerCase().includes(marketSearch.toLowerCase()));

  useEffect(() => {
    resultRef.current = result;
  }, [result]);
  useEffect(() => () => {
    initialRequestRef.current?.abort();
    pageRequestControllersRef.current.forEach((controller) => controller.abort());
    abortAll();
  }, [abortAll]);

  const loadMarkets = async () => {
    setLoadingMarkets(true);
    setError(null);
    setPageError(null);
    try {
      const response = await enqueue((signal) => fetchUpbitMarkets({ isDetails, signal }));
      setMarkets(response.markets);
      setMarket((current) => response.markets.some((item) => item.market === current) ? current : response.markets[0]?.market ?? "");
      setResult({ kind: "markets", markets: response.markets, raw: response.raw });
    } catch (caught) {
      if (!isAbortError(caught)) setError(messageFor(caught, "업비트 거래쌍 목록 조회 중 알 수 없는 오류가 발생했습니다."));
    } finally {
      setLoadingMarkets(false);
    }
  };

  const loadInitialCandles = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!market) return;
    initialRequestRef.current?.abort();
    pageRequestControllersRef.current.forEach((pageController) => pageController.abort());
    pageRequestControllersRef.current.clear();
    const requestId = ++candleRequestIdRef.current;
    const controller = new AbortController();
    initialRequestRef.current = controller;
    setLoadingCandles(true);
    setError(null);
    setPageError(null);
    try {
      const to = endTime ? kstLocalToIso(endTime) : undefined;
      const convertingPriceUnit = convertToKrw && interval === "1d" ? "KRW" as const : undefined;
      const response = await enqueue(
        (signal) => fetchUpbitCandles({
          market,
          interval,
          count: Number(count),
          to,
          ...(convertingPriceUnit ? { convertingPriceUnit } : {}),
          signal
        }),
        controller.signal
      );
      if (!controller.signal.aborted && candleRequestIdRef.current === requestId) {
        setResult({
          kind: "candles", market, interval, candles: response.candles, raw: response.raw,
          hasEndTime: Boolean(to), convertingPriceUnit, requestId,
          convertedClose: findLatestConvertedClose(response.candles, response.raw, convertingPriceUnit),
          exhausted: { past: response.candles.length === 0, future: !to || response.candles.length === 0 }
        });
      }
    } catch (caught) {
      if (!isAbortError(caught)) setError(messageFor(caught, "업비트 캔들 조회 중 알 수 없는 오류가 발생했습니다."));
    } finally {
      if (initialRequestRef.current === controller) {
        initialRequestRef.current = null;
        setLoadingCandles(false);
      }
    }
  };

  const loadPage = async (direction: PageDirection) => {
    const current = resultRef.current;
    if (current?.kind !== "candles" || current.exhausted[direction] || pageDirectionsRef.current.has(direction) || current.candles.length === 0) return;
    if (direction === "future" && !current.hasEndTime) return;

    const controller = new AbortController();
    pageRequestControllersRef.current.add(controller);
    pageDirectionsRef.current.add(direction);
    setPageLoading(direction);
    setPageError(null);
    const edge = direction === "past" ? current.candles[0] : current.candles.at(-1);
    const to = direction === "past" ? edge?.startedAt : edge ? advanceByPage(edge.startedAt, current.interval) : undefined;
    try {
      const response = await enqueue((signal) => fetchUpbitCandles({
        market: current.market, interval: current.interval, count: 200, to,
        ...(current.convertingPriceUnit ? { convertingPriceUnit: current.convertingPriceUnit } : {}), signal
      }), controller.signal);
      if (candleRequestIdRef.current !== current.requestId) return;
      setResult((previous) => {
        if (previous?.kind !== "candles" || previous.requestId !== current.requestId) return previous;
        const page = response.candles;
        const merged = mergeUpbitCandles(previous.candles, page);
        const exhausted = response.candles.length === 0 || merged.length === previous.candles.length;
        return {
          ...previous,
          candles: merged,
          raw: [...previous.raw, ...response.raw],
          convertedClose: findLatestConvertedClose(merged, [...previous.raw, ...response.raw], previous.convertingPriceUnit),
          exhausted: { ...previous.exhausted, [direction]: exhausted }
        };
      });
    } catch (caught) {
      if (!isAbortError(caught)) setPageError(messageFor(caught, "추가 캔들 조회에 실패했습니다. 표시 중인 캔들은 유지됩니다."));
    } finally {
      pageRequestControllersRef.current.delete(controller);
      pageDirectionsRef.current.delete(direction);
      setPageLoading(null);
      setEdgeRequestVersion((version) => version + 1);
    }
  };

  return (
    <section className="upbit-api-test-page" aria-label="업비트 API 테스트 화면">
      <section className="panel upbit-api-test-intro">
        <div><p className="eyebrow">개발·검증 도구</p><h2>업비트 API 테스트</h2><p>공개 REST API를 브라우저에서 직접 호출합니다. Origin 요청은 10초에 한 번만 실행합니다.</p></div>
        <a href="https://docs.upbit.com/kr/reference/list-candles-minutes" target="_blank" rel="noreferrer">업비트 캔들 API 문서</a>
      </section>

      <section className="panel upbit-api-request-panel" aria-label="업비트 요청 패널">
        <div className="upbit-api-tabs" role="tablist" aria-label="업비트 API 종류">
          <button type="button" role="tab" aria-selected={tab === "markets"} onClick={() => setTab("markets")}>거래쌍 목록</button>
          <button type="button" role="tab" aria-selected={tab === "candles"} onClick={() => setTab("candles")}>캔들</button>
        </div>
        {tab === "markets" ? (
          <div className="upbit-api-test-form upbit-market-form">
            <label className="upbit-checkbox"><input aria-label="상세 정보 포함" type="checkbox" checked={isDetails} onChange={(event) => setDetails(event.target.checked)} />상세 정보 포함</label>
            <button type="button" onClick={() => void loadMarkets()} disabled={isLoadingMarkets}>{isLoadingMarkets ? "조회 중…" : "거래쌍 목록 조회"}</button>
          </div>
        ) : (
          <form className="upbit-api-test-form" onSubmit={loadInitialCandles} aria-label="업비트 캔들 조회 조건">
            <label>거래쌍<select aria-label="거래쌍" value={market} disabled={markets.length === 0} onChange={(event) => setMarket(event.target.value)}><option value="">거래쌍 목록을 먼저 조회하세요</option>{markets.map((item) => <option key={item.market} value={item.market}>{item.market} · {item.koreanName}</option>)}</select></label>
            <label>캔들 주기<select aria-label="캔들 주기" value={interval} disabled={markets.length === 0} onChange={(event) => setInterval(event.target.value as UpbitCandleInterval)}>{intervals.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
            <label>조회 개수<input aria-label="조회 개수" type="number" min="1" max="200" value={count} disabled={markets.length === 0} onChange={(event) => setCount(event.target.value)} /></label>
            <label>종료 시각 (KST)<input aria-label="종료 시각 (KST)" type="datetime-local" value={endTime} disabled={markets.length === 0} onChange={(event) => setEndTime(event.target.value)} /></label>
            {interval === "1d" ? <label className="upbit-checkbox"><input aria-label="원화 환산" type="checkbox" checked={convertToKrw} disabled={markets.length === 0} onChange={(event) => setConvertToKrw(event.target.checked)} />일봉 원화 환산</label> : null}
            <button type="submit" disabled={markets.length === 0 || isLoadingCandles}>{isLoadingCandles ? "조회 중…" : "캔들 조회"}</button>
          </form>
        )}
      </section>

      {waitingSeconds !== null ? <p className="upbit-rate-limit-wait" role="status" aria-live="polite">업비트 요청 대기 중: {waitingSeconds}초</p> : null}
      {error ? <p className="analysis-error" role="alert">{error}</p> : null}
      {!error && result === null && !isLoadingMarkets && !isLoadingCandles ? <p className="upbit-api-test-empty">요청 탭을 선택하고 업비트 데이터를 조회해 주세요.</p> : null}
      {isLoadingMarkets ? <p className="upbit-api-test-empty">거래쌍 목록을 조회하는 중입니다.</p> : null}
      {isLoadingCandles ? <p className="upbit-api-test-empty">캔들을 조회하는 중입니다.</p> : null}
      {result?.kind === "markets" ? <MarketResult markets={visibleMarkets} raw={result.raw} search={marketSearch} onSearch={setMarketSearch} /> : null}
      {result?.kind === "candles" && candles.length === 0 ? <p className="upbit-api-test-empty">해당 조건에는 업비트 캔들 데이터가 없습니다.</p> : null}
      {result?.kind === "candles" && candles.length > 0 ? <>
        <section className="panel upbit-api-test-chart-panel"><div className="panel-heading"><div><h2>{result.market} {intervals.find((item) => item.value === result.interval)?.label} 캔들</h2><span>업비트 응답 {candles.length.toLocaleString("ko-KR")}개 · 시간 오름차순</span></div><strong>{latest ? `₩${formatNumber(String(latest.close))}` : "-"}</strong></div>
          <UpbitCandleChart candles={candles} indicators={indicators} edgeRequestVersion={edgeRequestVersion} onRequestEdge={(direction) => void loadPage(direction)} />
          <div className="analysis-legend"><span>SMA 20</span><span>EMA 20</span><span>볼린저 밴드 (20, 2)</span><span>거래량</span>{pageLoading ? <span>추가 캔들 조회 중…</span> : null}</div>
          <button className="upbit-page-button" type="button" disabled={result.exhausted.past || pageLoading !== null} onClick={() => void loadPage("past")}>과거 캔들 더 보기</button>
          {pageError ? <p className="analysis-error" role="alert">{pageError}</p> : null}
        </section>
        <section className="upbit-api-test-grid" aria-label="최신 OHLCV와 보조지표"><Metric label="시가" value={latest?.open} /><Metric label="고가" value={latest?.high} /><Metric label="저가" value={latest?.low} /><Metric label="종가" value={latest?.close} />{result.convertingPriceUnit ? <Metric label="원화 환산 종가" value={result.convertedClose} /> : null}<Metric label="체결량" value={latest?.volume} /><Metric label="체결대금" value={latest?.tradeAmount} /><Metric label="SMA 20" value={latestIndicator?.sma20 ?? null} /><Metric label="EMA 20" value={latestIndicator?.ema20 ?? null} /><Metric label="RSI 14" value={latestIndicator?.rsi14 ?? null} /></section>
        <AccessibleCandleTable candles={candles} />
        <RawJson label="캔들 원본 JSON" value={result.raw} />
      </> : null}
    </section>
  );
}

function MarketResult({ markets, raw, search, onSearch }: { markets: UpbitMarket[]; raw: unknown[]; search: string; onSearch: (value: string) => void }) {
  return <><section className="panel upbit-market-result"><label>거래쌍 검색<input aria-label="거래쌍 검색" value={search} onChange={(event) => onSearch(event.target.value)} placeholder="KRW-BTC 또는 비트코인" /></label><div className="upbit-table-scroll"><table><thead><tr><th>거래쌍</th><th>한글명</th><th>영문명</th><th>주의</th></tr></thead><tbody>{markets.map((item) => <tr key={item.market}><td>{item.market}</td><td>{item.koreanName}</td><td>{item.englishName}</td><td>{item.marketWarning ?? "-"}</td></tr>)}</tbody></table></div>{markets.length === 0 ? <p className="upbit-api-test-empty">검색 조건에 맞는 거래쌍이 없습니다.</p> : null}</section><RawJson label="거래쌍 목록 원본 JSON" value={raw} /></>;
}

function RawJson({ label, value }: { label: string; value: unknown }) { return <details className="panel upbit-api-test-raw"><summary>{label}</summary><pre aria-label={label}>{JSON.stringify(value, null, 2)}</pre></details>; }
function Metric({ label, value }: { label: string; value: number | null | undefined }) { return <article className="panel"><span>{label}</span><strong>{value === null || value === undefined ? "-" : formatNumber(String(value))}</strong></article>; }
function AccessibleCandleTable({ candles }: { candles: UpbitCandle[] }) { return <details className="panel upbit-api-test-table"><summary>최근 10개 OHLCV 표 보기</summary><div className="upbit-table-scroll"><table><thead><tr><th scope="col">시각 (KST)</th><th scope="col">시가</th><th scope="col">고가</th><th scope="col">저가</th><th scope="col">종가</th><th scope="col">체결량</th></tr></thead><tbody>{candles.slice(-10).reverse().map((candle) => <tr key={candle.startedAt}><td>{formatKst(candle.startedAt)}</td><td>{formatNumber(String(candle.open))}</td><td>{formatNumber(String(candle.high))}</td><td>{formatNumber(String(candle.low))}</td><td>{formatNumber(String(candle.close))}</td><td>{formatNumber(String(candle.volume))}</td></tr>)}</tbody></table></div></details>; }

function calculateIndicators(candles: UpbitCandle[]): Indicator[] { let ema: number | null = null; return candles.map((candle, index) => { const closes = candles.slice(0, index + 1).map((item) => item.close); const recent20 = closes.slice(-20); const sma20 = recent20.length === 20 ? average(recent20) : null; ema = ema === null ? candle.close : candle.close * (2 / 21) + ema * (19 / 21); const deviation = sma20 === null ? null : Math.sqrt(average(recent20.map((value) => (value - sma20) ** 2))); return { startedAt: candle.startedAt, sma20, ema20: ema, bollingerUpper: sma20 === null || deviation === null ? null : sma20 + deviation * 2, bollingerMiddle: sma20, bollingerLower: sma20 === null || deviation === null ? null : sma20 - deviation * 2, rsi14: closes.length < 15 ? null : rsi(closes.slice(-15)) }; }); }
function average(values: number[]): number { return values.reduce((sum, value) => sum + value, 0) / values.length; }
function rsi(values: number[]): number { const changes = values.slice(1).map((value, index) => value - values[index]); const gain = average(changes.map((value) => Math.max(0, value))); const loss = average(changes.map((value) => Math.max(0, -value))); return loss === 0 ? 100 : 100 - 100 / (1 + gain / loss); }
function formatKst(value: string): string { return new Date(value).toLocaleString("ko-KR", { timeZone: "Asia/Seoul", hourCycle: "h23" }); }
function findLatestConvertedClose(candles: UpbitCandle[], raw: unknown[], convertingPriceUnit?: "KRW"): number | undefined {
  if (!convertingPriceUnit || candles.length === 0) return undefined;
  const convertedPrices = new Map(raw.flatMap((item) => {
    if (typeof item !== "object" || item === null) return [];
    const value = item as Record<string, unknown>;
    if (typeof value.candle_date_time_utc !== "string" || typeof value.converted_trade_price !== "number") return [];
    return [[new Date(value.candle_date_time_utc.endsWith("Z") ? value.candle_date_time_utc : `${value.candle_date_time_utc}Z`).toISOString().replace(".000Z", "Z"), value.converted_trade_price] as const];
  }));
  return convertedPrices.get(candles.at(-1)?.startedAt ?? "");
}
function isAbortError(error: unknown): boolean { return error instanceof DOMException && error.name === "AbortError"; }
function messageFor(error: unknown, fallback: string): string { return error instanceof Error ? error.message : fallback; }
function kstLocalToIso(value: string): string { return new Date(`${value.length === 16 ? `${value}:00` : value}+09:00`).toISOString(); }
function advanceByPage(value: string, interval: UpbitCandleInterval): string { const date = new Date(value); if (interval === "1M") date.setUTCMonth(date.getUTCMonth() + 200); else if (interval === "1w") date.setUTCDate(date.getUTCDate() + 7 * 200); else if (interval === "1d") date.setUTCDate(date.getUTCDate() + 200); else { const minutes = interval === "1h" ? 60 : interval === "4h" ? 240 : Number(interval.replace("m", "")); date.setUTCMinutes(date.getUTCMinutes() + minutes * 200); } return date.toISOString(); }

type QueuedRequest = { controller: AbortController; timer: ReturnType<typeof setTimeout> | null; reject: (reason: unknown) => void; scheduledAt: number };
function useUpbitRequestQueue() {
  const requestsRef = useRef(new Set<QueuedRequest>());
  const [waitingSeconds, setWaitingSeconds] = useState<number | null>(null);
  const refreshWaiting = useCallback(() => { const scheduled = [...requestsRef.current].map((item) => item.scheduledAt).filter((item) => item > Date.now()); setWaitingSeconds(scheduled.length ? Math.ceil((Math.min(...scheduled) - Date.now()) / 1000) : null); }, []);
  const abortAll = useCallback(() => { for (const request of requestsRef.current) { request.controller.abort(); if (request.timer) clearTimeout(request.timer); request.reject(new DOMException("요청이 취소되었습니다.", "AbortError")); } requestsRef.current.clear(); refreshWaiting(); }, [refreshWaiting]);
  useEffect(() => { const timer = window.setInterval(refreshWaiting, 250); return () => window.clearInterval(timer); }, [refreshWaiting]);
  const enqueue = useCallback(<T,>(work: (signal: AbortSignal) => Promise<T>, externalSignal?: AbortSignal) => new Promise<T>((resolve, reject) => {
    const controller = new AbortController();
    const scheduledAt = Math.max(Date.now(), nextUpbitBrowserRequestAt);
    nextUpbitBrowserRequestAt = scheduledAt + 10_000;
    const request: QueuedRequest = { controller, timer: null, reject, scheduledAt };
    const remove = () => { requestsRef.current.delete(request); refreshWaiting(); };
    const abort = () => { if (request.timer) clearTimeout(request.timer); remove(); reject(new DOMException("요청이 취소되었습니다.", "AbortError")); };
    controller.signal.addEventListener("abort", abort, { once: true });
    externalSignal?.addEventListener("abort", () => controller.abort(), { once: true });
    const run = () => {
      if (controller.signal.aborted) return;
      request.timer = null;
      refreshWaiting();
      work(controller.signal).then(
        (value) => { remove(); resolve(value); },
        (reason) => { remove(); reject(reason); }
      );
    };
    requestsRef.current.add(request);
    const delay = scheduledAt - Date.now();
    if (delay <= 0) run(); else request.timer = setTimeout(run, delay);
    refreshWaiting();
  }), [refreshWaiting]);
  return {
    waitingSeconds,
    abortAll,
    enqueue
  };
}
