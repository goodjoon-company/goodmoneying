import { useEffect, useRef } from "react";
import { CandlestickSeries, ColorType, createChart, HistogramSeries, LineSeries, type UTCTimestamp } from "lightweight-charts";
import type { UpbitCandle } from "../upbitApi";

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

export function UpbitCandleChart({ candles, indicators, edgeRequestVersion, onRequestEdge }: { candles: UpbitCandle[]; indicators: Indicator[]; edgeRequestVersion: number; onRequestEdge: (direction: PageDirection) => void }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const adapterRef = useRef<{ update: (nextCandles: UpbitCandle[], nextIndicators: Indicator[]) => void } | null>(null);
  const notifiedEdgesRef = useRef(new Set<PageDirection>());
  const callbackRef = useRef(onRequestEdge);
  const candleCountRef = useRef(candles.length);
  callbackRef.current = onRequestEdge;
  candleCountRef.current = candles.length;

  useEffect(() => {
    notifiedEdgesRef.current.clear();
  }, [candles.length, edgeRequestVersion]);

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
    let hasFitted = false;
    adapterRef.current = { update: (nextCandles, nextIndicators) => {
      candleSeries.setData(nextCandles.map((item) => ({ time: toTime(item.startedAt), open: item.open, high: item.high, low: item.low, close: item.close })));
      volume.setData(nextCandles.map((item) => ({ time: toTime(item.startedAt), value: item.volume, color: item.close >= item.open ? "rgba(53,220,167,.32)" : "rgba(237,108,98,.30)" })));
      lines.forEach(([key, series]) => series.setData(nextIndicators.filter((item) => item[key] !== null).map((item) => ({ time: toTime(item.startedAt), value: item[key] as number }))));
      if (!hasFitted) { chart.timeScale().fitContent(); hasFitted = true; }
    } };
    chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (!range) return;
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
  }, []);
  useEffect(() => adapterRef.current?.update(candles, indicators), [candles, indicators]);
  return <div className="upbit-api-test-chart" ref={containerRef} aria-label="업비트 API 캔들 차트" />;
}

function toTime(value: string): UTCTimestamp { return Math.floor(new Date(value).getTime() / 1000) as UTCTimestamp; }
