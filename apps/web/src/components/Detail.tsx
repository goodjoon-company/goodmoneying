import { useEffect, useMemo, useRef } from "react";
import { X } from "lucide-react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  HistogramSeries,
  type UTCTimestamp
} from "lightweight-charts";
import { type Candle, type Instrument, type InstrumentDetail, type OperationsSnapshot } from "../api";
import { formatFreshness, formatNumber, formatPercent } from "../operationsDisplay";
import { formatAssetAmount, formatKstDateTime, formatMoney } from "../displayFormat";
import { InstrumentTitle, MiniMetric, sampleCandles, statusText } from "./common";

export function DetailModal({
  snapshot,
  onClose
}: {
  snapshot: OperationsSnapshot;
  onClose: () => void;
}) {
  if (!snapshot.detail) {
    return (
      <div className="modal-backdrop">
        <section className="detail-modal" role="dialog" aria-label="코인 상세" aria-modal="true">
          <button className="icon-button close-button" type="button" aria-label="닫기" onClick={onClose}>
            <X size={18} />
          </button>
          <main className="loading-state">코인 상세를 불러오는 중</main>
        </section>
      </div>
    );
  }
  return (
    <div className="modal-backdrop">
      <section className="detail-modal" role="dialog" aria-label="코인 상세" aria-modal="true">
        <button className="icon-button close-button" type="button" aria-label="닫기" onClick={onClose}>
          <X size={18} />
        </button>
        <Detail detail={snapshot.detail} candles={snapshot.candles} />
      </section>
    </div>
  );
}

function Detail({ detail, candles: rawCandles }: { detail: InstrumentDetail; candles: Candle[] }) {
  const candles = useMemo(() => sampleCandles(rawCandles, 180), [rawCandles]);
  const instrument = detail.instrument;
  const sourceCoverage = detail.coverage.find((item) => item.dataType === "source_candle");
  return (
    <section className="detail-page">
      <h2 className="detail-title"><InstrumentTitle instrument={instrument} /></h2>
      <section className="panel chart-panel">
        <div className="panel-heading">
          <h2><InstrumentTitle instrument={instrument} /> 캔들·거래대금</h2>
          <span>2026년 1월 1분봉</span>
        </div>
        <TradingViewCandleChart
          candles={candles}
          instrument={instrument}
          currentPrice={detail.latestTicker.tradePrice}
        />
        <div className="detail-stats">
          <MiniMetric label="현재가" value={formatMoney(detail.latestTicker.tradePrice, instrument.quoteCurrency)} detail={formatFreshness(detail.latestTicker.collectedAt)} />
          <MiniMetric
            label="24H 변동금액"
            value={formatMoney(detail.priceChangeAmount24h, instrument.quoteCurrency)}
            detail={formatPercent(detail.priceChangeRate24h)}
          />
          <MiniMetric
            label="24H 거래량"
            value={formatAssetAmount(detail.tradeVolume24h, instrument.baseAsset)}
            detail={formatPercent(detail.tradeVolumeChangeRate24h)}
          />
          <MiniMetric
            label="캔들 커버리지"
            value={`${formatNumber(sourceCoverage?.progressPercent ?? "0")}%`}
            detail={sourceCoverage?.status ?? "unknown"}
          />
        </div>
      </section>
      <section className="panel orderbook-panel">
        <div className="panel-heading">
          <h2>호가 요약</h2>
          <span>{formatFreshness(detail.latestOrderbook.collectedAt)}</span>
        </div>
        <div className="orderbook-grid">
          <MiniMetric label="최우선 매수" value={formatMoney(detail.latestOrderbook.bestBidPrice, instrument.quoteCurrency)} detail={`수량 ${formatAssetAmount(detail.latestOrderbook.bestBidSize, instrument.baseAsset)}`} />
          <MiniMetric label="최우선 매도" value={formatMoney(detail.latestOrderbook.bestAskPrice, instrument.quoteCurrency)} detail={`수량 ${formatAssetAmount(detail.latestOrderbook.bestAskSize, instrument.baseAsset)}`} />
          <MiniMetric label="스프레드" value={formatMoney(detail.latestOrderbook.spread, instrument.quoteCurrency)} detail="정상 범위" />
          <MiniMetric label="호가 불균형" value={formatPercent(detail.latestOrderbook.imbalance10)} detail="매수 잔량 우세" />
        </div>
      </section>
      <section className="panel quality-history-panel">
        <div className="panel-heading">
          <h2>수집 품질 이력</h2>
          <span>{detail.qualityHistory.length}개</span>
        </div>
        <div className="quality-history-list">
          {detail.qualityHistory.map((event) => (
            <article className="quality-history-item" key={`${event.title}-${event.occurredAt}`}>
              <span className={`quality ${event.status}`}>{statusText(event.status)}</span>
              <div>
                <strong>{event.title}</strong>
                <em>{formatFreshness(event.occurredAt)}</em>
              </div>
              <p>{event.detail}</p>
            </article>
          ))}
        </div>
      </section>
    </section>
  );
}

function TradingViewCandleChart({
  candles,
  instrument,
  currentPrice
}: {
  candles: Candle[];
  instrument: Instrument;
  currentPrice: string;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!containerRef.current || candles.length === 0 || typeof ResizeObserver === "undefined") {
      return;
    }
    const container = containerRef.current;
    const chartTimeFormatter = (time: unknown) => formatKstDateTime(Number(time) * 1000);
    const chart = createChart(container, {
      width: container.clientWidth || 900,
      height: 328,
      localization: { timeFormatter: chartTimeFormatter },
      layout: {
        background: { type: ColorType.Solid, color: "#0c1010" },
        textColor: "#9ca7a0"
      },
      grid: {
        vertLines: { color: "rgba(148, 163, 184, 0.12)" },
        horzLines: { color: "rgba(148, 163, 184, 0.12)" }
      },
      rightPriceScale: { borderColor: "rgba(148, 163, 184, 0.2)" },
      timeScale: { borderColor: "rgba(148, 163, 184, 0.2)", timeVisible: true, secondsVisible: true, tickMarkFormatter: chartTimeFormatter }
    });
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#22c7a5",
      downColor: "#ff4d5a",
      borderVisible: false,
      wickUpColor: "#22c7a5",
      wickDownColor: "#ff4d5a",
      priceFormat: { type: "custom", minMove: 0.00000001, formatter: (price: number) => formatMoney(price, instrument.quoteCurrency) }
    });
    candleSeries.setData(
      candles.map((item) => ({
        time: Math.floor(new Date(item.startedAt).getTime() / 1000) as UTCTimestamp,
        open: Number(item.open),
        high: Number(item.high),
        low: Number(item.low),
        close: Number(item.close)
      }))
    );
    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "volume"
    });
    volumeSeries.setData(
      candles.map((item) => ({
        time: Math.floor(new Date(item.startedAt).getTime() / 1000) as UTCTimestamp,
        value: Number(item.volume),
        color: Number(item.close) >= Number(item.open) ? "rgba(34, 199, 165, 0.42)" : "rgba(255, 77, 90, 0.42)"
      }))
    );
    chart.priceScale("volume").applyOptions({ scaleMargins: { top: 0.78, bottom: 0 } });
    chart.timeScale().fitContent();
    const observer = new ResizeObserver(([entry]) => {
      chart.applyOptions({ width: Math.floor(entry.contentRect.width) });
    });
    observer.observe(container);
    return () => {
      observer.disconnect();
      chart.remove();
    };
  }, [candles, instrument.quoteCurrency]);

  return (
    <div className="trading-chart-shell" aria-label="TradingView 캔들 차트">
      <div className="chart-titlebar">
        <span>{instrument.baseAsset} / {instrument.quoteCurrency} · 1분 · UpBit</span>
        <strong>{formatMoney(currentPrice, instrument.quoteCurrency)}</strong>
      </div>
      <div className="chart-canvas" ref={containerRef}>
        {candles.length === 0 ? <span>선택한 기간에 저장된 캔들이 없습니다.</span> : null}
      </div>
      <div className="price-gauge">
        <span>현재가 게이지</span>
        <strong>{formatMoney(currentPrice, instrument.quoteCurrency)}</strong>
      </div>
      <div className="volume-gauge">
        <span>거래량 게이지</span>
        <strong>{candles.length > 0 ? formatAssetAmount(candles.at(-1)?.volume ?? "0", instrument.baseAsset) : `0 ${instrument.baseAsset}`}</strong>
      </div>
      <div className="trading-watermark">TradingView Lightweight Charts</div>
    </div>
  );
}
