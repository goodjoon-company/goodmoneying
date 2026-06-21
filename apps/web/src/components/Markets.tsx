import { useQuery } from "@tanstack/react-query";
import { loadMarketList, type MarketListRow, type OperationsSnapshot } from "../api";
import { formatFreshness, formatNumber, formatPercent } from "../operationsDisplay";
import { CoverageMeter, InstrumentName, TimeInline, statusText } from "./common";

const EMPTY_MARKET_ROWS: MarketListRow[] = [];

export function Markets({
  snapshot,
  selectedInstrumentId,
  onSelectInstrument
}: {
  snapshot: OperationsSnapshot;
  selectedInstrumentId: number | null;
  onSelectInstrument: (instrumentId: number) => void;
}) {
  const marketQuery = useQuery({
    queryKey: ["market-list"],
    queryFn: loadMarketList
  });
  const rows: MarketListRow[] = marketQuery.data ?? EMPTY_MARKET_ROWS;
  return (
    <section className="panel full">
      <div className="panel-heading">
        <h2>수집 데이터 요약</h2>
        <span>{rows.length}개</span>
      </div>
      <div className="data-table">
        <div className="table-header">
          <span>거래 상품</span>
          <span>현재가</span>
          <span>24시간 거래대금</span>
          <span>등락률</span>
          <span>최신성</span>
          <span>커버리지</span>
          <span>저장 행</span>
          <span>품질</span>
        </div>
        {rows.length === 0 ? <p className="helper-text">시장 리스트를 불러오는 중입니다.</p> : null}
        {rows.map((row) => (
          <button
            className={`table-row market-row-button ${
              selectedInstrumentId === row.instrument.id ? "selected" : ""
            }`}
            key={row.instrument.id}
            type="button"
            onClick={() => onSelectInstrument(row.instrument.id)}
          >
            <InstrumentName instrument={row.instrument} />
            <span>{formatNumber(row.tradePrice)}</span>
            <span>{row.accTradePrice24hDisplay}</span>
            <span className={Number(row.changeRate) >= 0 ? "change up" : "change down"}>
              {formatPercent(row.changeRate)}
            </span>
            <TimeInline value={formatFreshness(row.tickerCollectedAt)} zone="KST" />
            <CoverageMeter value={row.coveragePercent} />
            <span>{row.storageRowCount.toLocaleString("ko-KR")}</span>
            <span className={`quality ${row.qualityStatus}`}>{statusText(row.qualityStatus)}</span>
          </button>
        ))}
      </div>
    </section>
  );
}
