import { useEffect, useRef } from "react";
import { CandlestickSeries, ColorType, createChart, HistogramSeries, LineSeries, type UTCTimestamp } from "lightweight-charts";
import type { CandleRow as UpbitCandle } from "./upbit-api-test/types";
import { formatKstDateTime, formatMoney } from "../displayFormat";

export type Indicator = {
  startedAt: string;
  sma20: number | null;
  ema20: number | null;
  bollingerUpper: number | null;
  bollingerMiddle: number | null;
  bollingerLower: number | null;
  rsi14: number | null;
};

type PageDirection = "past" | "future";

export function UpbitCandleChart({ candles, indicators, edgeRequestVersion, onRequestEdge, quoteCurrency }: { candles: UpbitCandle[]; indicators: Indicator[]; edgeRequestVersion: number; onRequestEdge: (direction: PageDirection) => void; quoteCurrency: string }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const adapterRef = useRef<{ update: (nextCandles: UpbitCandle[], nextIndicators: Indicator[]) => void } | null>(null);
  const notifiedEdgesRef = useRef(new Set<PageDirection>());
  const callbackRef = useRef(onRequestEdge);
  const candleCountRef = useRef(candles.length);
  const edgeRequestVersionRef = useRef(edgeRequestVersion);
  const hasUserNavigatedRef = useRef(false);
  callbackRef.current = onRequestEdge;
  candleCountRef.current = candles.length;
  edgeRequestVersionRef.current = edgeRequestVersion;

  useEffect(() => {
    notifiedEdgesRef.current.clear();
  }, [candles.length, edgeRequestVersion]);

  useEffect(() => {
    if (!containerRef.current || typeof ResizeObserver === "undefined") return;
    const container = containerRef.current;
    const chart = createChart(container, { width: container.clientWidth || 900, height: 440, localization: { timeFormatter: chartTimeFormatter }, layout: { background: { type: ColorType.Solid, color: "#101713" }, textColor: "#8e9e94" }, grid: { vertLines: { color: "rgba(255,255,255,.05)" }, horzLines: { color: "rgba(255,255,255,.06)" } }, rightPriceScale: { borderColor: "rgba(255,255,255,.12)" }, timeScale: { borderColor: "rgba(255,255,255,.12)", timeVisible: true, secondsVisible: true, tickMarkFormatter: chartTimeFormatter } });
    const candleSeries = chart.addSeries(CandlestickSeries, { upColor: "#35dca7", downColor: "#ed6c62", borderVisible: false, wickUpColor: "#35dca7", wickDownColor: "#ed6c62", priceFormat: { type: "custom", minMove: 0.00000001, formatter: (price: number) => formatMoney(price, quoteCurrency) } });
    const volume = chart.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "volume" });
    chart.priceScale("volume").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    const lines = ([
      ["sma20", "#f2c96f"], ["ema20", "#62b5ff"], ["bollingerUpper", "#7896c9"], ["bollingerMiddle", "#6c7fa8"], ["bollingerLower", "#7896c9"]
    ] as const).map(([key, color]) => [key, chart.addSeries(LineSeries, { color, lineWidth: 1, lineStyle: key.startsWith("bollinger") ? 2 : 0, lastValueVisible: false, priceLineVisible: false })] as const);
    let hasFitted = false;
    let previousFirstStartedAt: string | null = null;
    let previousCount = 0;
    adapterRef.current = { update: (nextCandles, nextIndicators) => {
      const visibleRange = hasFitted ? chart.timeScale().getVisibleLogicalRange() : null;
      const prependedCount = visibleRange && previousFirstStartedAt !== null &&
        nextCandles[0]?.startedAt.localeCompare(previousFirstStartedAt) < 0
        ? Math.max(0, nextCandles.length - previousCount)
        : 0;
      candleSeries.setData(nextCandles.map((item) => ({ time: toTime(item.startedAt), open: item.open, high: item.high, low: item.low, close: item.close })));
      volume.setData(nextCandles.map((item) => ({ time: toTime(item.startedAt), value: item.volume, color: item.close >= item.open ? "rgba(53,220,167,.32)" : "rgba(237,108,98,.30)" })));
      lines.forEach(([key, series]) => series.setData(nextIndicators.filter((item) => item[key] !== null).map((item) => ({ time: toTime(item.startedAt), value: item[key] as number }))));
      if (!hasFitted) { chart.timeScale().fitContent(); hasFitted = true; }
      else if (visibleRange && prependedCount > 0) {
        chart.timeScale().setVisibleLogicalRange({
          from: visibleRange.from + prependedCount,
          to: visibleRange.to + prependedCount
        });
      }
      previousFirstStartedAt = nextCandles[0]?.startedAt ?? null;
      previousCount = nextCandles.length;
    } };
    chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (!range || edgeRequestVersionRef.current === 0 || !hasUserNavigatedRef.current) return;
      const lastIndex = Math.max(0, candleCountRef.current - 1);
      const direction = range.from <= 2 ? "past" : range.to >= lastIndex - 2 ? "future" : null;
      if (direction && !notifiedEdgesRef.current.has(direction)) {
        notifiedEdgesRef.current.add(direction);
        callbackRef.current(direction);
      }
    });
    const observer = new ResizeObserver(([entry]) => chart.applyOptions({ width: Math.floor(entry.contentRect.width) }));
    observer.observe(container);
    return () => { observer.disconnect(); adapterRef.current = null; chart.remove(); };
  }, [quoteCurrency]);
  useEffect(() => adapterRef.current?.update(candles, indicators), [candles, indicators]);
  return <div className="upbit-api-test-chart" ref={containerRef} aria-label="업비트 API 캔들 차트"
    onPointerDown={() => { hasUserNavigatedRef.current = true; }}
    onWheel={() => { hasUserNavigatedRef.current = true; }} />;
}

function toTime(value: string): UTCTimestamp { return Math.floor(new Date(value).getTime() / 1000) as UTCTimestamp; }

function chartTimeFormatter(time: unknown): string {
  return formatKstDateTime(Number(time) * 1000);
}
