import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, ArrowUpDown, Search, Star } from "lucide-react";
import { updateFavoriteTargets, type CandidateUniverseEntry, type MarketListRow } from "../api";
import { formatCurrencyAmount, formatPercent } from "../operationsDisplay";
import { CoverageMeter, InstrumentName } from "./common";

const EMPTY_MARKET_ROWS: MarketListRow[] = [];
type MarketSortKey = "favorite" | "name" | "price" | "trade" | "change" | "basis" | "coverage" | "candles";
type SortDirection = "asc" | "desc";

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
  const [searchText, setSearchText] = useState("");
  const [sortKey, setSortKey] = useState<MarketSortKey>("trade");
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc");
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
      await queryClient.cancelQueries({ queryKey: ["candidate-universe"] });
      const previousRows = queryClient.getQueryData<MarketListRow[]>(["market-list"]);
      const previousUniverse = queryClient.getQueryData<CandidateUniverseEntry[]>([
        "candidate-universe"
      ]);
      queryClient.setQueryData<MarketListRow[]>(["market-list"], (currentRows) =>
        applyFavoriteOrder(currentRows ?? marketRows, nextIds)
      );
      queryClient.setQueryData<CandidateUniverseEntry[]>(["candidate-universe"], (currentEntries) =>
        applyFavoriteOrderToCandidateUniverse(currentEntries ?? [], nextIds)
      );
      return { previousRows, previousUniverse };
    },
    onError: (_error, _nextIds, context) => {
      if (context?.previousRows) {
        queryClient.setQueryData(["market-list"], context.previousRows);
      }
      if (context?.previousUniverse) {
        queryClient.setQueryData(["candidate-universe"], context.previousUniverse);
      }
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["candidate-universe"] });
      void queryClient.invalidateQueries({ queryKey: ["operations"] });
    }
  });
  const visibleRows = useMemo(() => {
    const filteredRows = coinRows.filter((row) => marketRowMatchesSearch(row, searchText));
    return sortMarketRows(filteredRows, sortKey, sortDirection);
  }, [coinRows, searchText, sortDirection, sortKey]);
  const favoriteRows = visibleRows.filter((row) => row.isFavorite);
  const candidateRows = visibleRows.filter((row) => !row.isFavorite);
  const favoriteCount = coinRows.filter((row) => row.isFavorite).length;
  const changeRateBasis = latestMarketTickerBasis(visibleRows);

  const setSort = (nextKey: MarketSortKey) => {
    if (sortKey === nextKey) {
      setSortDirection((current) => (current === "desc" ? "asc" : "desc"));
      return;
    }
    setSortKey(nextKey);
    setSortDirection(nextKey === "trade" || nextKey === "change" || nextKey === "coverage" || nextKey === "candles" ? "desc" : "asc");
  };

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
    setSortKey("favorite");
    setSortDirection("asc");
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
      <div className="target-toolbar market-toolbar">
        <label>
          <Search size={16} />
          <input
            placeholder="종목명 또는 심볼 검색"
            value={searchText}
            onChange={(event) => setSearchText(event.target.value)}
          />
        </label>
      </div>
      <div className="data-table">
        <div className="table-header">
          <MarketSortButton
            active={sortKey === "favorite"}
            direction={sortDirection}
            label="관심 추가"
            onClick={() => setSort("favorite")}
          />
          <MarketSortButton
            active={sortKey === "name"}
            direction={sortDirection}
            label="거래 상품"
            onClick={() => setSort("name")}
          />
          <MarketSortButton
            active={sortKey === "price"}
            direction={sortDirection}
            label="현재가"
            onClick={() => setSort("price")}
          />
          <MarketSortButton
            active={sortKey === "trade"}
            direction={sortDirection}
            label="24시간 거래대금"
            onClick={() => setSort("trade")}
          />
          <MarketSortButton
            active={sortKey === "change"}
            direction={sortDirection}
            label={`등락률 ${changeRateBasis} 기준`}
            onClick={() => setSort("change")}
          />
          <MarketSortButton
            active={sortKey === "basis"}
            direction={sortDirection}
            label="기준일시"
            onClick={() => setSort("basis")}
          />
          <MarketSortButton
            active={sortKey === "coverage"}
            direction={sortDirection}
            label="캔들 커버리지"
            onClick={() => setSort("coverage")}
          />
          <MarketSortButton
            active={sortKey === "candles"}
            direction={sortDirection}
            label="1분 캔들 수"
            onClick={() => setSort("candles")}
          />
        </div>
        {visibleRows.length === 0 ? (
          <p className="helper-text">
            {searchText ? "검색 조건에 맞는 관심종목이 없습니다." : "관심종목을 불러오는 중입니다."}
          </p>
        ) : null}
        {favoriteRows.length > 0 ? <MarketSectionHeading label="관심추가 항목" count={favoriteRows.length} /> : null}
        {favoriteRows.map((row) => (
          <MarketRow
            favoriteCount={favoriteCount}
            key={row.instrument.id}
            row={row}
            selected={selectedInstrumentId === row.instrument.id}
            onMoveFavorite={moveFavorite}
            onOpen={openRow}
            onToggleFavorite={toggleFavorite}
          />
        ))}
        {candidateRows.length > 0 ? <MarketSectionHeading label="후보 종목" count={candidateRows.length} /> : null}
        {candidateRows.map((row) => (
          <MarketRow
            favoriteCount={favoriteCount}
            key={row.instrument.id}
            row={row}
            selected={selectedInstrumentId === row.instrument.id}
            onMoveFavorite={moveFavorite}
            onOpen={openRow}
            onToggleFavorite={toggleFavorite}
          />
        ))}
      </div>
      {favoriteMutation.isError ? (
        <p className="error-text">관심목록 변경에 실패했습니다.</p>
      ) : null}
    </section>
  );
}

function MarketSortButton({
  active,
  direction,
  label,
  onClick
}: {
  active: boolean;
  direction: SortDirection;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={`dashboard-sort-button ${active ? "active" : ""}`}
      aria-label={`${label} 정렬`}
      aria-sort={active ? (direction === "desc" ? "descending" : "ascending") : "none"}
      onClick={onClick}
    >
      <span>{label}</span>
      <ArrowUpDown size={13} />
    </button>
  );
}

function MarketSectionHeading({ label, count }: { label: string; count: number }) {
  return (
    <div className="table-section-heading">
      <strong>{label}</strong>
      <span>{count.toLocaleString("ko-KR")}개</span>
    </div>
  );
}

function MarketRow({
  favoriteCount,
  row,
  selected,
  onMoveFavorite,
  onOpen,
  onToggleFavorite
}: {
  favoriteCount: number;
  row: MarketListRow;
  selected: boolean;
  onMoveFavorite: (row: MarketListRow, direction: "up" | "down") => void;
  onOpen: (row: MarketListRow) => void;
  onToggleFavorite: (row: MarketListRow) => void;
}) {
  return (
    <div className={`table-row ${selected ? "selected" : ""}`}>
      <span className="favorite-controls">
        <button
          className={`favorite-toggle ${row.isFavorite ? "active" : ""}`}
          type="button"
          aria-label={`${row.instrument.baseAsset} ${row.isFavorite ? "관심 제거" : "관심 추가"}`}
          disabled={!row.isFavorite && favoriteCount >= 50}
          onClick={(event) => {
            event.stopPropagation();
            onToggleFavorite(row);
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
            onMoveFavorite(row, "up");
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
            onMoveFavorite(row, "down");
          }}
        >
          <ArrowDown size={14} />
        </button>
      </span>
      <button
        className="market-row-button"
        type="button"
        aria-disabled={row.tickerCollectedAt === null}
        onClick={() => onOpen(row)}
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

function applyFavoriteOrderToCandidateUniverse(
  entries: CandidateUniverseEntry[],
  favoriteIds: number[]
): CandidateUniverseEntry[] {
  const orderById = new Map(favoriteIds.map((id, index) => [id, index + 1]));
  return entries.map((entry) => {
    const favoriteOrder = orderById.get(entry.instrument.id) ?? null;
    return {
      ...entry,
      selected: favoriteOrder !== null,
      favoriteOrder,
      isRealtimeTarget: favoriteOrder !== null
    };
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

function sortMarketRows(
  rows: MarketListRow[],
  sortKey: MarketSortKey,
  direction: SortDirection
): MarketListRow[] {
  return [...rows].sort((left, right) => {
    const order = compareMarketRows(left, right, sortKey);
    const directedOrder = direction === "desc" ? -order : order;
    if (directedOrder !== 0) return directedOrder;
    if (left.isFavorite !== right.isFavorite) return left.isFavorite ? -1 : 1;
    return left.instrument.baseAsset.localeCompare(right.instrument.baseAsset, "ko-KR");
  });
}

function compareMarketRows(left: MarketListRow, right: MarketListRow, sortKey: MarketSortKey) {
  if (sortKey === "favorite") {
    return favoriteOrderValue(left) - favoriteOrderValue(right);
  }
  if (sortKey === "name") {
    return left.instrument.baseAsset.localeCompare(right.instrument.baseAsset, "ko-KR");
  }
  if (sortKey === "price") {
    return nullableNumber(left.tradePrice) - nullableNumber(right.tradePrice);
  }
  if (sortKey === "change") {
    return nullableNumber(left.changeRate) - nullableNumber(right.changeRate);
  }
  if (sortKey === "basis") {
    return nullableTime(left.tickerCollectedAt) - nullableTime(right.tickerCollectedAt);
  }
  if (sortKey === "coverage") {
    return Number(left.coveragePercent) - Number(right.coveragePercent);
  }
  if (sortKey === "candles") {
    return left.oneMinuteCandleCount - right.oneMinuteCandleCount;
  }
  return nullableNumber(left.accTradePrice24h) - nullableNumber(right.accTradePrice24h);
}

function favoriteOrderValue(row: MarketListRow) {
  if (row.favoriteOrder !== null) return row.favoriteOrder;
  return Number.MAX_SAFE_INTEGER;
}

function nullableNumber(value: string | null) {
  if (value === null) return Number.NEGATIVE_INFINITY;
  return Number(value);
}

function nullableTime(value: string | null) {
  if (value === null) return Number.NEGATIVE_INFINITY;
  return Date.parse(value);
}

function marketRowMatchesSearch(row: MarketListRow, searchText: string) {
  const normalizedSearch = searchText.trim().toLocaleLowerCase("ko-KR");
  if (!normalizedSearch) return true;
  const haystack = [
    row.instrument.marketCode,
    row.instrument.baseAsset,
    row.instrument.quoteCurrency,
    row.instrument.displayName
  ]
    .join(" ")
    .toLocaleLowerCase("ko-KR");
  return haystack.includes(normalizedSearch);
}

function latestMarketTickerBasis(rows: MarketListRow[]) {
  const latest = rows
    .map((row) => nullableTime(row.tickerCollectedAt))
    .filter((value) => Number.isFinite(value))
    .sort((left, right) => right - left)[0];
  return Number.isFinite(latest) ? formatKstShort(new Date(latest).toISOString()) : "기준 없음";
}

function formatKstShort(value: string) {
  const parts = new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false
  }).formatToParts(new Date(value));
  const get = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((part) => part.type === type)?.value ?? "";
  return `${get("month")}.${get("day")} ${get("hour")}:${get("minute")} KST`;
}
