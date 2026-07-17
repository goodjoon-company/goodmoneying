import { useEffect, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  HistogramSeries,
  LineSeries,
  type UTCTimestamp
} from "lightweight-charts";
import type { MarketListRow } from "../api";
import { type AnalysisRangeDays, type AnalysisUnit } from "../analysisStream";
import { formatFreshness, formatNumber, formatPercent } from "../operationsDisplay";
import { formatAssetAmount, formatKstDateTime, formatMoney } from "../displayFormat";
import { useRealtimeAnalysis } from "../useRealtimeAnalysis";
import { InstrumentTitle } from "./common";

const timeframes: { value: AnalysisUnit; label: string }[] = [
  { value: "1M", label: "월봉" },
  { value: "1w", label: "주봉" },
  { value: "1d", label: "일봉" },
  { value: "1h", label: "시봉" },
  { value: "4h", label: "4시간" },
  { value: "30m", label: "30분" },
  { value: "10m", label: "10분" },
  { value: "15m", label: "15분" },
  { value: "5m", label: "5분" },
  { value: "3m", label: "3분" },
  { value: "1m", label: "1분" }
];

const ranges: { value: AnalysisRangeDays; label: string }[] = [
  { value: 1, label: "1일" },
  { value: 7, label: "1주" },
  { value: 30, label: "1개월" },
  { value: 90, label: "3개월" },
  { value: 365, label: "1년" },
  { value: 1095, label: "3년" }
];

export function CoinAnalysis({
  rows,
  onOpenWatchlist
}: {
  rows: MarketListRow[];
  onOpenWatchlist: () => void;
}) {
  const [instrumentId, setInstrumentId] = useState<number | null>(rows[0]?.instrument.id ?? null);
  const [unit, setUnit] = useState<AnalysisUnit>("1d");
  const [rangeDays, setRangeDays] = useState<AnalysisRangeDays>(365);
  const analysis = useRealtimeAnalysis(instrumentId, unit, rangeDays);

  useEffect(() => {
    if (instrumentId === null && rows[0]) setInstrumentId(rows[0].instrument.id);
  }, [instrumentId, rows]);

  if (rows.length === 0) {
    return (
      <section className="analysis-page empty-analysis">
        <h2>관심 코인을 먼저 선택해 주세요.</h2>
        <p>전체 코인 목록에서 분석할 코인을 관심목록에 넣은 뒤 이 화면으로 돌아오면 됩니다.</p>
        <button type="button" onClick={onOpenWatchlist}>관심 코인 관리 열기</button>
      </section>
    );
  }

  const selected = rows.find((row) => row.instrument.id === instrumentId) ?? rows[0];
  const latestCandle = analysis.candles.at(-1);
  return (
    <section className="analysis-page" aria-label="코인 분석 화면">
      <aside className="analysis-watchlist panel">
        <div className="panel-heading"><h2>관심 코인 선택</h2><span>{rows.length}개</span></div>
        <div className="analysis-coin-list">
          {rows.map((row) => (
            <button
              key={row.instrument.id}
              type="button"
              className={row.instrument.id === selected.instrument.id ? "active" : ""}
              aria-label={`${row.instrument.baseAsset} 분석`}
              onClick={() => setInstrumentId(row.instrument.id)}
            >
              <span><InstrumentTitle instrument={row.instrument} /></span>
              <strong>{formatMoney(row.tradePrice ?? "0", row.priceCurrency)}</strong>
              <em>{formatPercent(row.changeRate ?? "0")}</em>
            </button>
          ))}
        </div>
      </aside>
      <div className="analysis-workspace">
        <header className="analysis-header">
          <div>
            <p className="eyebrow">관심목록 · 실시간 분석</p>
            <h2><InstrumentTitle instrument={analysis.instrument ?? selected.instrument} /></h2>
          </div>
          <span className={`analysis-connection ${analysis.connectionStatus}`}>{connectionLabel(analysis.connectionStatus)}</span>
        </header>
        <div className="analysis-controls" aria-label="차트 시간 제어">
          <div className="segmented-control" aria-label="캔들 시간 단위">
            {timeframes.map((item) => <button key={item.value} type="button" className={unit === item.value ? "active" : ""} aria-pressed={unit === item.value} onClick={() => setUnit(item.value)}>{item.label}</button>)}
          </div>
          <div className="segmented-control" aria-label="차트 기간">
            {ranges.map((item) => <button key={item.value} type="button" className={rangeDays === item.value ? "active" : ""} aria-pressed={rangeDays === item.value} onClick={() => setRangeDays(item.value)}>{item.label}</button>)}
          </div>
        </div>
        {analysis.error ? <p className="analysis-error">{analysis.error}</p> : null}
        <section className="analysis-chart-panel panel">
          <div className="panel-heading"><div><h2>가격 · 거래량 · 추세</h2><span>{timeframeLabel(unit)} · {rangeLabel(rangeDays)} 요청 · {analysis.candles.length.toLocaleString("ko-KR")}개 표시{isHighFrequency(unit) ? " (최근 1,000개 한도)" : ""}</span></div><strong>{analysis.market ? formatMoney(analysis.market.ticker.tradePrice, selected.instrument.quoteCurrency) : "연결 중"}</strong></div>
          <AnalysisChart candles={analysis.candles} indicators={analysis.indicators} quoteCurrency={selected.instrument.quoteCurrency} />
          <div className="analysis-lineage" aria-label="집계 계보와 품질">
            계산 {latestCandle?.calculationVersion ?? "대기"} · 품질 {latestCandle?.quality ?? "unverified"} · 완전성 {latestCandle?.completeness ?? "empty"}
            {latestCandle ? <small>원천 기준 {formatKstDateTime(latestCandle.sourceAsOf)} · 지식 시각 {formatKstDateTime(latestCandle.knowledgeAt)}</small> : null}
          </div>
          <div className="analysis-legend"><span>SMA 20</span><span>SMA 60</span><span>EMA 20</span><span>볼린저 밴드</span><span>거래량</span></div>
        </section>
        <section className="analysis-market-grid" aria-label="현재가 호가 체결">
          <MarketCard title="현재가" value={analysis.market ? formatMoney(analysis.market.ticker.tradePrice, selected.instrument.quoteCurrency) : "-"} detail={analysis.market ? `${formatPercent(analysis.market.ticker.changeRate)} · 24H ${formatMoney(analysis.market.ticker.accTradePrice24h, selected.instrument.quoteCurrency)}` : "WebSocket 대기"} />
          <MarketCard title="호가 요약" value={analysis.market ? `${formatMoney(analysis.market.orderbook.bestBidPrice, selected.instrument.quoteCurrency)} / ${formatMoney(analysis.market.orderbook.bestAskPrice, selected.instrument.quoteCurrency)}` : "-"} detail={analysis.market ? `매수 ${formatAssetAmount(analysis.market.orderbook.bestBidSize, selected.instrument.baseAsset)} · 매도 ${formatAssetAmount(analysis.market.orderbook.bestAskSize, selected.instrument.baseAsset)} · 스프레드 ${formatMoney(analysis.market.orderbook.spread, selected.instrument.quoteCurrency)}` : "호가 대기"} />
          <MarketCard title="10호가 · 불균형" value={analysis.market ? `${formatPercent(analysis.market.orderbook.imbalance10)}` : "-"} detail={analysis.market ? `매수 ${formatAssetAmount(analysis.market.orderbook.bidDepth10, selected.instrument.baseAsset)} · 매도 ${formatAssetAmount(analysis.market.orderbook.askDepth10, selected.instrument.baseAsset)}` : "호가 대기"} />
          <MarketCard title="체결 흐름" value={analysis.market ? `${analysis.market.tradeSummary.tradeCount.toLocaleString("ko-KR")}건` : "-"} detail={analysis.market ? `매수 ${formatAssetAmount(analysis.market.tradeSummary.buyVolume, selected.instrument.baseAsset)} · 매도 ${formatAssetAmount(analysis.market.tradeSummary.sellVolume, selected.instrument.baseAsset)} · ${analysis.market.tradeSummary.lastTradeAt ? formatFreshness(analysis.market.tradeSummary.lastTradeAt) : "체결 없음"}` : "체결 대기"} />
          <MarketCard title="RSI 14" value={analysis.indicators.at(-1)?.rsi14 ? formatNumber(analysis.indicators.at(-1)?.rsi14 ?? "0") : "-"} detail="최근 종가 기준 모멘텀" />
        </section>
      </div>
    </section>
  );
}

function AnalysisChart({ candles, indicators, quoteCurrency }: { candles: ReturnType<typeof useRealtimeAnalysis>["candles"]; indicators: ReturnType<typeof useRealtimeAnalysis>["indicators"]; quoteCurrency: string }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartAdapterRef = useRef<{
    update: (nextCandles: typeof candles, nextIndicators: typeof indicators) => void;
  } | null>(null);
  useEffect(() => {
    if (!containerRef.current || typeof ResizeObserver === "undefined") return;
    const container = containerRef.current;
    const chartTimeFormatter = (time: unknown) => formatKstDateTime(Number(time) * 1000);
    const chart = createChart(container, { width: container.clientWidth || 900, height: 450, localization: { timeFormatter: chartTimeFormatter }, layout: { background: { type: ColorType.Solid, color: "#101713" }, textColor: "#8e9e94" }, grid: { vertLines: { color: "rgba(255,255,255,.05)" }, horzLines: { color: "rgba(255,255,255,.06)" } }, rightPriceScale: { borderColor: "rgba(255,255,255,.12)" }, timeScale: { borderColor: "rgba(255,255,255,.12)", timeVisible: true, secondsVisible: true, tickMarkFormatter: chartTimeFormatter } });
    const candleSeries = chart.addSeries(CandlestickSeries, { upColor: "#35dca7", downColor: "#ed6c62", borderVisible: false, wickUpColor: "#35dca7", wickDownColor: "#ed6c62", priceFormat: { type: "custom", minMove: 0.00000001, formatter: (price: number) => formatMoney(price, quoteCurrency) } });
    const volume = chart.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "volume" });
    chart.priceScale("volume").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    const lines = [["sma20", "#f2c96f"], ["sma60", "#b58cff"], ["ema20", "#62b5ff"], ["bollingerUpper", "#7896c9"], ["bollingerMiddle", "#6c7fa8"], ["bollingerLower", "#7896c9"]] as const;
    const lineSeries = lines.map(([key, color]) => [key, chart.addSeries(LineSeries, { color, lineWidth: 1, lineStyle: key.startsWith("bollinger") ? 2 : 0, lastValueVisible: false, priceLineVisible: false })] as const);
    let fitted = false;
    chartAdapterRef.current = {
      update: (nextCandles, nextIndicators) => {
        candleSeries.setData(nextCandles.map((item) => ({ time: toTime(item.startedAt), open: Number(item.open), high: Number(item.high), low: Number(item.low), close: Number(item.close) })));
        volume.setData(nextCandles.map((item) => ({ time: toTime(item.startedAt), value: Number(item.volume), color: Number(item.close) >= Number(item.open) ? "rgba(53,220,167,.32)" : "rgba(237,108,98,.30)" })));
        lineSeries.forEach(([key, series]) => series.setData(nextIndicators.filter((point) => point[key] !== null).map((point) => ({ time: toTime(point.startedAt), value: Number(point[key]) }))));
        if (nextCandles.length === 0) fitted = false;
        if (!fitted && nextCandles.length > 0) {
          chart.timeScale().fitContent();
          fitted = true;
        }
      }
    };
    const observer = new ResizeObserver(([entry]) => chart.applyOptions({ width: Math.floor(entry.contentRect.width) }));
    observer.observe(container);
    return () => { observer.disconnect(); chartAdapterRef.current = null; chart.remove(); };
  }, [quoteCurrency]);
  useEffect(() => { chartAdapterRef.current?.update(candles, indicators); }, [candles, indicators]);
  return <div className="analysis-chart-canvas" ref={containerRef} aria-label="코인 분석 캔들 차트">{candles.length === 0 ? <span>선택한 기간의 저장 차트가 없습니다. Backfill 관리에서 기간을 수집하면 이 위치에 차트와 보조지표가 표시됩니다.</span> : null}</div>;
}

function MarketCard({ title, value, detail }: { title: string; value: string; detail: string }) {
  return <article className="analysis-market-card panel"><span>{title}</span><strong>{value}</strong><small>{detail}</small></article>;
}

function connectionLabel(status: "connecting" | "live" | "offline"): string {
  return status === "live" ? "WebSocket 실시간" : status === "connecting" ? "WebSocket 연결 중" : "WebSocket 재연결 대기";
}

function timeframeLabel(unit: AnalysisUnit): string {
  return timeframes.find((item) => item.value === unit)?.label ?? unit;
}

function rangeLabel(rangeDays: AnalysisRangeDays): string {
  return ranges.find((item) => item.value === rangeDays)?.label ?? `${rangeDays}일`;
}

function isHighFrequency(unit: AnalysisUnit): boolean {
  return ["1m", "3m", "5m", "10m", "15m", "30m", "1h", "4h"].includes(unit);
}

function toTime(value: string): UTCTimestamp { return Math.floor(new Date(value).getTime() / 1000) as UTCTimestamp; }
