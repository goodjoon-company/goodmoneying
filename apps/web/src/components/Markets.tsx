import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Star } from "lucide-react";
import { updateFavoriteTargets, type MarketListRow } from "../api";
import { formatCurrencyAmount, formatPercent } from "../operationsDisplay";
import { CoverageMeter, InstrumentName } from "./common";

const EMPTY_MARKET_ROWS: MarketListRow[] = [];

export function Markets({
  rows,
  selectedInstrumentId,
  onSelectInstrument
}: {
  rows: MarketListRow[];
  selectedInstrumentId: number | null;
  onSelectInstrument: (instrumentId: number) => void;
}) {
  const queryClient = useQueryClient();
  const [assetType, setAssetType] = useState<"coin" | "stock">("coin");
  const marketRows = rows.length > 0 ? rows : EMPTY_MARKET_ROWS;
  const coinRows = useMemo(() => marketRows.filter((row) => row.assetType === "coin"), [marketRows]);
  const favoriteIds = useMemo(
    () => coinRows.filter((row) => row.isFavorite).map((row) => row.instrument.id),
    [coinRows]
  );
  const favoriteMutation = useMutation({
    mutationFn: updateFavoriteTargets,
    onMutate: async (nextIds) => {
      await queryClient.cancelQueries({ queryKey: ["market-list"] });
      const previousRows = queryClient.getQueryData<MarketListRow[]>(["market-list"]);
      queryClient.setQueryData<MarketListRow[]>(["market-list"], (currentRows) =>
        applyFavoriteOrder(currentRows ?? marketRows, nextIds)
      );
      return { previousRows };
    },
    onError: (_error, _nextIds, context) => {
      if (context?.previousRows) {
        queryClient.setQueryData(["market-list"], context.previousRows);
      }
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["candidate-universe"] });
      void queryClient.invalidateQueries({ queryKey: ["operations"] });
    }
  });
  const visibleRows = assetType === "coin" ? coinRows : [];
  const favoriteCount = coinRows.filter((row) => row.isFavorite).length;

  const toggleFavorite = (row: MarketListRow) => {
    const nextIds = row.isFavorite
      ? favoriteIds.filter((id) => id !== row.instrument.id)
      : [...favoriteIds, row.instrument.id];
    favoriteMutation.mutate(nextIds);
  };

  const moveFavorite = (row: MarketListRow, direction: "up" | "down") => {
    const index = favoriteIds.indexOf(row.instrument.id);
    const nextIndex = direction === "up" ? index - 1 : index + 1;
    if (index < 0 || nextIndex < 0 || nextIndex >= favoriteIds.length) return;
    const nextIds = [...favoriteIds];
    [nextIds[index], nextIds[nextIndex]] = [nextIds[nextIndex], nextIds[index]];
    favoriteMutation.mutate(nextIds);
  };

  const openRow = (row: MarketListRow) => {
    if (row.tickerCollectedAt === null) return;
    onSelectInstrument(row.instrument.id);
  };

  return (
    <section className="panel full">
      <div className="panel-heading">
        <h2>관심종목</h2>
        <span>{favoriteCount}개</span>
      </div>
      <div className="segmented-control" aria-label="자산 구분">
        <button
          type="button"
          aria-pressed={assetType === "coin"}
          onClick={() => setAssetType("coin")}
        >
          코인
        </button>
        <button
          type="button"
          aria-pressed={assetType === "stock"}
          onClick={() => setAssetType("stock")}
        >
          주식
        </button>
      </div>
      <div className="data-table">
        <div className="table-header">
          <span>관심 추가</span>
          <span>거래 상품</span>
          <span>현재가</span>
          <span>24시간 거래대금</span>
          <span>등락률(전일 종가 대비)</span>
          <span>기준일시</span>
          <span>캔들 커버리지</span>
          <span>1분 캔들 수</span>
        </div>
        {assetType === "stock" ? (
          <p className="helper-text">표시할 주식 관심종목이 없습니다.</p>
        ) : null}
        {assetType === "coin" && visibleRows.length === 0 ? (
          <p className="helper-text">관심종목을 불러오는 중입니다.</p>
        ) : null}
        {visibleRows.map((row) => (
          <div
            className={`table-row ${selectedInstrumentId === row.instrument.id ? "selected" : ""}`}
            key={row.instrument.id}
          >
            <span className="favorite-controls">
              <button
                className={`favorite-toggle ${row.isFavorite ? "active" : ""}`}
                type="button"
                aria-label={`${row.instrument.baseAsset} ${row.isFavorite ? "관심 제거" : "관심 추가"}`}
                disabled={!row.isFavorite && favoriteCount >= 50}
                onClick={(event) => {
                  event.stopPropagation();
                  toggleFavorite(row);
                }}
              >
                <Star size={16} fill={row.isFavorite ? "currentColor" : "none"} />
              </button>
              <button
                className="order-move-button"
                type="button"
                aria-label={`${row.instrument.baseAsset} 관심 순서 위로`}
                disabled={!row.isFavorite || row.favoriteOrder === 1}
                onClick={(event) => {
                  event.stopPropagation();
                  moveFavorite(row, "up");
                }}
              >
                <ArrowUp size={14} />
              </button>
              <button
                className="order-move-button"
                type="button"
                aria-label={`${row.instrument.baseAsset} 관심 순서 아래로`}
                disabled={!row.isFavorite || row.favoriteOrder === null || row.favoriteOrder >= favoriteCount}
                onClick={(event) => {
                  event.stopPropagation();
                  moveFavorite(row, "down");
                }}
              >
                <ArrowDown size={14} />
              </button>
            </span>
            <button
              className="market-row-button"
              type="button"
              aria-disabled={row.tickerCollectedAt === null}
              onClick={() => openRow(row)}
            >
              <InstrumentName instrument={row.instrument} />
            </button>
            <MoneyCell value={row.tradePrice} currency={row.priceCurrency} />
            <MoneyCell value={row.accTradePrice24h} currency={row.tradeAmountCurrency} />
            <span className={Number(row.changeRate ?? "0") >= 0 ? "change up" : "change down"}>
              {row.changeRate === null ? "-" : formatPercent(row.changeRate)}
            </span>
            <span>{row.tickerCollectedAt ? formatKstDateTime(row.tickerCollectedAt) : "-"}</span>
            <CoverageWithRange row={row} />
            <span>{row.oneMinuteCandleCount.toLocaleString("ko-KR")}</span>
          </div>
        ))}
      </div>
      {favoriteMutation.isError ? (
        <p className="error-text">관심목록 변경에 실패했습니다.</p>
      ) : null}
    </section>
  );
}

function applyFavoriteOrder(rows: MarketListRow[], favoriteIds: number[]): MarketListRow[] {
  const orderById = new Map(favoriteIds.map((id, index) => [id, index + 1]));
  return rows
    .map((row) => {
      const favoriteOrder = orderById.get(row.instrument.id) ?? null;
      return {
        ...row,
        isFavorite: favoriteOrder !== null,
        favoriteOrder
      };
    })
    .sort((left, right) => {
      if (left.isFavorite !== right.isFavorite) return left.isFavorite ? -1 : 1;
      if (left.favoriteOrder !== null && right.favoriteOrder !== null) {
        return left.favoriteOrder - right.favoriteOrder;
      }
      return left.instrument.id - right.instrument.id;
    });
}

function MoneyCell({ value, currency }: { value: string | null; currency: string }) {
  if (value === null) {
    return <span>-</span>;
  }
  return (
    <span className="money-cell">
      <strong>{formatCurrencyAmount(value, currency)}</strong>
      <em>{currency}</em>
    </span>
  );
}

function CoverageWithRange({ row }: { row: MarketListRow }) {
  return (
    <span className="coverage-with-range">
      <CoverageMeter value={row.coveragePercent} />
      <span className="coverage-range">
        <small>{row.candleCoverageStartAt ? formatKstDate(row.candleCoverageStartAt) : "-"}</small>
        <small>{formatKstDate(row.candleCoverageCurrentAt)}</small>
      </span>
    </span>
  );
}

function formatKstDateTime(value: string): string {
  return new Date(value).toLocaleString("ko-KR", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23"
  });
}

function formatKstDate(value: string): string {
  return new Date(value).toLocaleDateString("ko-KR", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit"
  });
}
