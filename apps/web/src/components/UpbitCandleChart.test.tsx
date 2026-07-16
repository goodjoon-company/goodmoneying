import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { UpbitCandleChart } from "./UpbitCandleChart";

const chartSpy = vi.hoisted(() => ({
  rangeListener: undefined as undefined | ((range: { from: number; to: number } | null) => void),
  getVisibleLogicalRange: vi.fn(() => ({ from: 0, to: 1 })),
  setVisibleLogicalRange: vi.fn(),
  chartOptions: undefined as undefined | Record<string, any>,
  candleOptions: undefined as undefined | Record<string, any>
}));

vi.mock("lightweight-charts", () => ({
  CandlestickSeries: "CandlestickSeries",
  ColorType: { Solid: "solid" },
  HistogramSeries: "HistogramSeries",
  LineSeries: "LineSeries",
  createChart: vi.fn((_container, options) => {
    chartSpy.chartOptions = options;
    return ({
    addSeries: vi.fn((series, options) => {
      if (series === "CandlestickSeries") chartSpy.candleOptions = options;
      return { setData: vi.fn() };
    }),
    priceScale: vi.fn(() => ({ applyOptions: vi.fn() })),
    timeScale: vi.fn(() => ({
      fitContent: vi.fn(),
      getVisibleLogicalRange: chartSpy.getVisibleLogicalRange,
      setVisibleLogicalRange: chartSpy.setVisibleLogicalRange,
      subscribeVisibleLogicalRangeChange: vi.fn((listener) => { chartSpy.rangeListener = listener; })
    })),
    applyOptions: vi.fn(),
    remove: vi.fn()
  });
  })
}));

describe("UpbitCandleChart", () => {
  afterEach(cleanup);
  beforeEach(() => {
    chartSpy.rangeListener = undefined;
    chartSpy.getVisibleLogicalRange.mockClear();
    chartSpy.setVisibleLogicalRange.mockClear();
    chartSpy.chartOptions = undefined;
    chartSpy.candleOptions = undefined;
    vi.stubGlobal("ResizeObserver", class {
      observe() {}
      disconnect() {}
    });
  });

  it("KST 24시간 축과 호가 통화 가격 형식을 사용한다", () => {
    render(
      <UpbitCandleChart
        candles={[]}
        indicators={[]}
        edgeRequestVersion={0}
        onRequestEdge={vi.fn()}
        quoteCurrency="KRW"
      />
    );

    expect(chartSpy.chartOptions?.localization.timeFormatter(1784264767))
      .toBe("2026.07.17 14:06:07 KST");
    expect(chartSpy.chartOptions?.timeScale.tickMarkFormatter(1784264767))
      .toBe("2026.07.17 14:06:07 KST");
    expect(chartSpy.candleOptions?.priceFormat.formatter(1234567.9))
      .toBe("1,234,567 ￦");
  });

  it("같은 논리 범위 가장자리 알림은 방향별로 한 번만 전달한다", () => {
    const onRequestEdge = vi.fn();
    render(
      <UpbitCandleChart
        candles={Array.from({ length: 4 }, (_, index) => ({
          startedAt: `2026-07-14T00:0${index}:00Z`, open: 1, high: 2, low: 1, close: 2, volume: 3, tradeAmount: 6, raw: {}
        }))}
        indicators={[]}
        edgeRequestVersion={1}
        onRequestEdge={onRequestEdge}
        quoteCurrency="KRW"
      />
    );

    fireEvent.pointerDown(screen.getByLabelText("업비트 API 캔들 차트"));
    chartSpy.rangeListener?.({ from: 0, to: 1 });
    chartSpy.rangeListener?.({ from: 0, to: 1 });
    chartSpy.rangeListener?.({ from: 3, to: 4 });

    expect(onRequestEdge).toHaveBeenCalledTimes(2);
    expect(onRequestEdge).toHaveBeenNthCalledWith(1, "past");
    expect(onRequestEdge).toHaveBeenNthCalledWith(2, "future");
  });

  it("과거 캔들을 앞에 합칠 때 사용자가 보던 논리 범위를 유지한다", () => {
    const row = (startedAt: string) => ({
      startedAt, open: 1, high: 2, low: 1, close: 2, volume: 3, tradeAmount: 6, raw: {}
    });
    const { rerender } = render(
      <UpbitCandleChart candles={[row("2026-07-14T00:01:00Z"), row("2026-07-14T00:02:00Z")]}
        indicators={[]} edgeRequestVersion={1} onRequestEdge={vi.fn()} quoteCurrency="KRW" />
    );

    rerender(
      <UpbitCandleChart candles={[row("2026-07-14T00:00:00Z"), row("2026-07-14T00:01:00Z"), row("2026-07-14T00:02:00Z")]}
        indicators={[]} edgeRequestVersion={2} onRequestEdge={vi.fn()} quoteCurrency="KRW" />
    );

    expect(chartSpy.setVisibleLogicalRange).toHaveBeenCalledWith({ from: 1, to: 2 });
  });
});
