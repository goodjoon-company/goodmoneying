import { render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { UpbitCandleChart } from "./UpbitCandleChart";

const chartSpy = vi.hoisted(() => ({
  rangeListener: undefined as undefined | ((range: { from: number; to: number } | null) => void)
}));

vi.mock("lightweight-charts", () => ({
  CandlestickSeries: "CandlestickSeries",
  ColorType: { Solid: "solid" },
  HistogramSeries: "HistogramSeries",
  LineSeries: "LineSeries",
  createChart: vi.fn(() => ({
    addSeries: vi.fn(() => ({ setData: vi.fn() })),
    priceScale: vi.fn(() => ({ applyOptions: vi.fn() })),
    timeScale: vi.fn(() => ({
      fitContent: vi.fn(),
      subscribeVisibleLogicalRangeChange: vi.fn((listener) => { chartSpy.rangeListener = listener; })
    })),
    applyOptions: vi.fn(),
    remove: vi.fn()
  }))
}));

describe("UpbitCandleChart", () => {
  beforeEach(() => {
    chartSpy.rangeListener = undefined;
    vi.stubGlobal("ResizeObserver", class {
      observe() {}
      disconnect() {}
    });
  });

  it("같은 논리 범위 가장자리 알림은 방향별로 한 번만 전달한다", () => {
    const onRequestEdge = vi.fn();
    render(
      <UpbitCandleChart
        candles={Array.from({ length: 4 }, (_, index) => ({
          startedAt: `2026-07-14T00:0${index}:00Z`, open: 1, high: 2, low: 1, close: 2, volume: 3, tradeAmount: 6
        }))}
        indicators={[]}
        edgeRequestVersion={0}
        onRequestEdge={onRequestEdge}
      />
    );

    chartSpy.rangeListener?.({ from: 0, to: 1 });
    chartSpy.rangeListener?.({ from: 0, to: 1 });
    chartSpy.rangeListener?.({ from: 3, to: 4 });

    expect(onRequestEdge).toHaveBeenCalledTimes(2);
    expect(onRequestEdge).toHaveBeenNthCalledWith(1, "past");
    expect(onRequestEdge).toHaveBeenNthCalledWith(2, "future");
  });
});
