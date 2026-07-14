import { useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  HistogramSeries,
  LineSeries,
  type UTCTimestamp
} from "lightweight-charts";
import { formatNumber } from "../operationsDisplay";
import {
  fetchUpbitCandles,
  type UpbitCandle,
  type UpbitCandleInterval
} from "../upbitApi";

const intervals: { value: UpbitCandleInterval; label: string }[] = [
  { value: "1m", label: "1분" },
  { value: "3m", label: "3분" },
  { value: "5m", label: "5분" },
  { value: "10m", label: "10분" },
  { value: "15m", label: "15분" },
  { value: "30m", label: "30분" },
  { value: "1h", label: "1시간" },
  { value: "4h", label: "4시간" },
  { value: "1d", label: "일봉" },
  { value: "1w", label: "주봉" },
  { value: "1M", label: "월봉" }
];

type Indicator = {
  startedAt: string;
  sma20: number | null;
  ema20: number | null;
  bollingerUpper: number | null;
  bollingerMiddle: number | null;
  bollingerLower: number | null;
  rsi14: number | null;
};

type QueryResult = {
  market: string;
  interval: UpbitCandleInterval;
  candles: UpbitCandle[];
};

export function UpbitApiTest() {
  const [market, setMarket] = useState("KRW-BTC");
  const [interval, setInterval] = useState<UpbitCandleInterval>("1d");
  const [count, setCount] = useState("100");
  const [result, setResult] = useState<QueryResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setLoading] = useState(false);
  const requestRef = useRef<AbortController | null>(null);
  const candles = result?.candles ?? [];
  const indicators = useMemo(() => calculateIndicators(candles), [candles]);
  const latest = candles.at(-1);
  const latestIndicator = indicators.at(-1);

  useEffect(() => () => requestRef.current?.abort(), []);

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const candles = await fetchUpbitCandles({
        market,
        interval,
        count: Number(count),
        signal: controller.signal
      });
      if (requestRef.current === controller) {
        setResult({ market: market.trim().toUpperCase(), interval, candles });
      }
    } catch (caught) {
      if (requestRef.current === controller && !isAbortError(caught)) {
        setError(caught instanceof Error ? caught.message : "업비트 캔들 조회 중 알 수 없는 오류가 발생했습니다.");
      }
    } finally {
      if (requestRef.current === controller) {
        requestRef.current = null;
        setLoading(false);
      }
    }
  };

  return (
    <section className="upbit-api-test-page" aria-label="업비트 API 테스트 화면">
      <section className="panel upbit-api-test-intro">
        <div>
          <p className="eyebrow">개발·검증 도구</p>
          <h2>업비트 API 테스트</h2>
          <p>공개 REST API를 브라우저에서 직접 호출합니다. 내부 저장 데이터나 분석 WebSocket에는 영향을 주지 않습니다.</p>
        </div>
        <a href="https://docs.upbit.com/kr/reference/list-candles-minutes" target="_blank" rel="noreferrer">업비트 캔들 API 문서</a>
      </section>

      <form className="panel upbit-api-test-form" onSubmit={submit} aria-label="업비트 캔들 조회 조건">
        <label>
          거래쌍
          <input aria-label="거래쌍" value={market} onChange={(event) => setMarket(event.target.value)} placeholder="KRW-BTC" autoCapitalize="characters" />
        </label>
        <label>
          캔들 주기
          <select aria-label="캔들 주기" value={interval} onChange={(event) => setInterval(event.target.value as UpbitCandleInterval)}>
            {intervals.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
          </select>
        </label>
        <label>
          조회 개수
          <input aria-label="조회 개수" type="number" min="1" max="200" value={count} onChange={(event) => setCount(event.target.value)} />
        </label>
        <button type="submit" disabled={isLoading}>{isLoading ? "조회 중…" : "캔들 조회"}</button>
      </form>

      {error ? <p className="analysis-error" role="alert">{error}</p> : null}
      {!error && result === null && !isLoading ? <p className="upbit-api-test-empty">거래쌍과 주기를 선택한 뒤 캔들을 조회해 주세요.</p> : null}
      {!error && result !== null && candles.length === 0 ? <p className="upbit-api-test-empty">해당 조건에는 업비트 캔들 데이터가 없습니다.</p> : null}
      {candles.length > 0 ? (
        <>
          <section className="panel upbit-api-test-chart-panel">
            <div className="panel-heading">
              <div>
                <h2>{result?.market} {intervals.find((item) => item.value === result?.interval)?.label} 캔들</h2>
                <span>업비트 응답 {candles.length.toLocaleString("ko-KR")}개 · 시간 오름차순</span>
              </div>
              <strong>{latest ? `₩${formatNumber(String(latest.close))}` : "-"}</strong>
            </div>
            <UpbitCandleChart candles={candles} indicators={indicators} />
            <div className="analysis-legend"><span>SMA 20</span><span>EMA 20</span><span>볼린저 밴드 (20, 2)</span><span>거래량</span></div>
          </section>
          <section className="upbit-api-test-grid" aria-label="최신 OHLCV와 보조지표">
            <Metric label="시가" value={latest?.open} />
            <Metric label="고가" value={latest?.high} />
            <Metric label="저가" value={latest?.low} />
            <Metric label="종가" value={latest?.close} />
            <Metric label="체결량" value={latest?.volume} />
            <Metric label="체결대금" value={latest?.tradeAmount} />
            <Metric label="SMA 20" value={latestIndicator?.sma20 ?? null} />
            <Metric label="EMA 20" value={latestIndicator?.ema20 ?? null} />
            <Metric label="RSI 14" value={latestIndicator?.rsi14 ?? null} />
          </section>
          <AccessibleCandleTable candles={candles} />
        </>
      ) : null}
    </section>
  );
}

function AccessibleCandleTable({ candles }: { candles: UpbitCandle[] }) {
  return (
    <details className="panel upbit-api-test-table">
      <summary>최근 10개 OHLCV 표 보기</summary>
      <table>
        <thead><tr><th scope="col">시각 (KST)</th><th scope="col">시가</th><th scope="col">고가</th><th scope="col">저가</th><th scope="col">종가</th><th scope="col">체결량</th></tr></thead>
        <tbody>
          {candles.slice(-10).reverse().map((candle) => (
            <tr key={candle.startedAt}>
              <td>{formatKst(candle.startedAt)}</td><td>{formatNumber(String(candle.open))}</td><td>{formatNumber(String(candle.high))}</td><td>{formatNumber(String(candle.low))}</td><td>{formatNumber(String(candle.close))}</td><td>{formatNumber(String(candle.volume))}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

function UpbitCandleChart({ candles, indicators }: { candles: UpbitCandle[]; indicators: Indicator[] }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const adapterRef = useRef<{ update: (nextCandles: UpbitCandle[], nextIndicators: Indicator[]) => void } | null>(null);
  useEffect(() => {
    if (!containerRef.current || typeof ResizeObserver === "undefined") return;
    const container = containerRef.current;
    const chart = createChart(container, { width: container.clientWidth || 900, height: 440, layout: { background: { type: ColorType.Solid, color: "#101713" }, textColor: "#8e9e94" }, grid: { vertLines: { color: "rgba(255,255,255,.05)" }, horzLines: { color: "rgba(255,255,255,.06)" } }, rightPriceScale: { borderColor: "rgba(255,255,255,.12)" }, timeScale: { borderColor: "rgba(255,255,255,.12)", timeVisible: true } });
    const candleSeries = chart.addSeries(CandlestickSeries, { upColor: "#35dca7", downColor: "#ed6c62", borderVisible: false, wickUpColor: "#35dca7", wickDownColor: "#ed6c62" });
    const volume = chart.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "volume" });
    chart.priceScale("volume").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    const lines = ([
      ["sma20", "#f2c96f"], ["ema20", "#62b5ff"], ["bollingerUpper", "#7896c9"], ["bollingerMiddle", "#6c7fa8"], ["bollingerLower", "#7896c9"]
    ] as const).map(([key, color]) => [key, chart.addSeries(LineSeries, { color, lineWidth: 1, lineStyle: key.startsWith("bollinger") ? 2 : 0, lastValueVisible: false, priceLineVisible: false })] as const);
    adapterRef.current = {
      update: (nextCandles, nextIndicators) => {
        candleSeries.setData(nextCandles.map((item) => ({ time: toTime(item.startedAt), open: item.open, high: item.high, low: item.low, close: item.close })));
        volume.setData(nextCandles.map((item) => ({ time: toTime(item.startedAt), value: item.volume, color: item.close >= item.open ? "rgba(53,220,167,.32)" : "rgba(237,108,98,.30)" })));
        lines.forEach(([key, series]) => series.setData(nextIndicators.filter((item) => item[key] !== null).map((item) => ({ time: toTime(item.startedAt), value: item[key] as number }))));
        chart.timeScale().fitContent();
      }
    };
    const observer = new ResizeObserver(([entry]) => chart.applyOptions({ width: Math.floor(entry.contentRect.width) }));
    observer.observe(container);
    return () => { observer.disconnect(); adapterRef.current = null; chart.remove(); };
  }, []);
  useEffect(() => adapterRef.current?.update(candles, indicators), [candles, indicators]);
  return <div className="upbit-api-test-chart" ref={containerRef} aria-label="업비트 API 캔들 차트" />;
}

function Metric({ label, value }: { label: string; value: number | null | undefined }) {
  return <article className="panel"><span>{label}</span><strong>{value === null || value === undefined ? "-" : formatNumber(String(value))}</strong></article>;
}

function calculateIndicators(candles: UpbitCandle[]): Indicator[] {
  let ema: number | null = null;
  return candles.map((candle, index) => {
    const closes = candles.slice(0, index + 1).map((item) => item.close);
    const recent20 = closes.slice(-20);
    const sma20 = recent20.length === 20 ? average(recent20) : null;
    ema = ema === null ? candle.close : candle.close * (2 / 21) + ema * (19 / 21);
    const deviation = sma20 === null ? null : Math.sqrt(average(recent20.map((value) => (value - sma20) ** 2)));
    const rsi14 = closes.length < 15 ? null : rsi(closes.slice(-15));
    return {
      startedAt: candle.startedAt,
      sma20,
      ema20: ema,
      bollingerUpper: sma20 === null || deviation === null ? null : sma20 + deviation * 2,
      bollingerMiddle: sma20,
      bollingerLower: sma20 === null || deviation === null ? null : sma20 - deviation * 2,
      rsi14
    };
  });
}

function average(values: number[]): number {
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function rsi(values: number[]): number {
  const changes = values.slice(1).map((value, index) => value - values[index]);
  const gain = average(changes.map((value) => Math.max(0, value)));
  const loss = average(changes.map((value) => Math.max(0, -value)));
  return loss === 0 ? 100 : 100 - 100 / (1 + gain / loss);
}

function toTime(value: string): UTCTimestamp {
  return Math.floor(new Date(value).getTime() / 1000) as UTCTimestamp;
}

function formatKst(value: string): string {
  return new Date(value).toLocaleString("ko-KR", { timeZone: "Asia/Seoul", hourCycle: "h23" });
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}
