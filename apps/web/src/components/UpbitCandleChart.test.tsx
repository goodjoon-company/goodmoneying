import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { UpbitCandleChart } from "./UpbitCandleChart";

const chartSpy = vi.hoisted(() => ({
  rangeListener: undefined as undefined | ((range: { from: number; to: number } | null) => void),
  getVisibleLogicalRange: vi.fn(() => ({ from: 0, to: 1 })),
  setVisibleLogicalRange: vi.fn()
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
      getVisibleLogicalRange: chartSpy.getVisibleLogicalRange,
      setVisibleLogicalRange: chartSpy.setVisibleLogicalRange,
      subscribeVisibleLogicalRangeChange: vi.fn((listener) => { chartSpy.rangeListener = listener; })
    })),
    applyOptions: vi.fn(),
    remove: vi.fn()
  }))
}));

describe("UpbitCandleChart", () => {
  beforeEach(() => {
    chartSpy.rangeListener = undefined;
    chartSpy.getVisibleLogicalRange.mockClear();
    chartSpy.setVisibleLogicalRange.mockClear();
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
          startedAt: `2026-07-14T00:0${index}:00Z`, open: 1, high: 2, low: 1, close: 2, volume: 3, tradeAmount: 6, raw: {}
        }))}
        indicators={[]}
        edgeRequestVersion={1}
        onRequestEdge={onRequestEdge}
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
        indicators={[]} edgeRequestVersion={1} onRequestEdge={vi.fn()} />
    );

    rerender(
      <UpbitCandleChart candles={[row("2026-07-14T00:00:00Z"), row("2026-07-14T00:01:00Z"), row("2026-07-14T00:02:00Z")]}
        indicators={[]} edgeRequestVersion={2} onRequestEdge={vi.fn()} />
    );

    expect(chartSpy.setVisibleLogicalRange).toHaveBeenCalledWith({ from: 1, to: 2 });
  });
});
