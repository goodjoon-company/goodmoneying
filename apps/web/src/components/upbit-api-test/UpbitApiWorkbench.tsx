import { useEffect, useMemo, useRef, useState } from "react";
import { FileJson, X } from "lucide-react";
import { formatAssetAmount, formatKstDate, formatKstDateTime, formatMoney, formatNumber } from "../../displayFormat";

import { UpbitCandleChart } from "../UpbitCandleChart";
import { CatalogParameterField } from "./CatalogParameterField";
import { createUpbitGatewayClient, type UpbitGatewayClient } from "./client";
import { mergeCandleRows, nextCandleParameters, parseCandleRows, type CandleGranularity } from "./pagination";
import { describeTraceResponse } from "./trace";
import type {
  CandleRow,
  CatalogEndpoint,
  ParameterValue,
  RequestParameters,
  TraceEnvelope,
  WorkbenchContext,
  WorkbenchModuleExtension,
  WorkbenchModuleId
} from "./types";
import { validateParameterValues } from "./parameterInput";
import {
  buildInitialParameters,
  coerceParameterValue,
  isCommonParameter,
  quotationGroups,
  selectQuotationEndpoints,
  serializeParameters,
  type QuotationGroupId
} from "./workbench";

const defaultClient = createUpbitGatewayClient();

export function UpbitApiWorkbench({
  moduleId,
  client = defaultClient,
  market,
  onMarketChange,
  extensions = []
}: {
  moduleId: WorkbenchModuleId;
  client?: UpbitGatewayClient;
  market?: string;
  onMarketChange?: (market: string) => void;
  extensions?: WorkbenchModuleExtension[];
}) {
  const [internalMarket, setInternalMarket] = useState(market ?? "KRW-BTC");
  const context = useMemo(() => marketContext(market ?? internalMarket), [internalMarket, market]);
  const onContextChange = (next: WorkbenchContext) => {
    setInternalMarket(next.market);
    onMarketChange?.(next.market);
  };
  if (moduleId !== "quotation") {
    const extension = extensions.find((item) => item.id === moduleId);
    if (extension) return <section className="upbit-extension-shell">
      <WorkbenchCommonSelection context={context} marketOptions={[context.market]} onChange={onContextChange} />
      <extension.Component context={context} onContextChange={onContextChange} />
    </section>;
    const issue = moduleId === "exchange" ? "#22" : "#23";
    const title = moduleId === "exchange" ? "Exchange API" : "WebSocket API";
    return (
      <section className="upbit-workbench-placeholder panel" aria-label={`${title} 확장 슬롯`}>
        <p className="eyebrow">P2.2 · 확장 슬롯(Extension Slot)</p>
        <h2>{title} 모듈 연결 대기</h2>
        <p>공통 작업대 계약을 구현하는 Issue {issue} 모듈을 통합 이슈에서 연결합니다.</p>
      </section>
    );
  }
  return <QuotationWorkbench client={client} context={context} onContextChange={onContextChange} />;
}

function QuotationWorkbench({ client, context, onContextChange }: {
  client: UpbitGatewayClient;
  context: WorkbenchContext;
  onContextChange: (context: WorkbenchContext) => void;
}) {
  const [catalog, setCatalog] = useState<Awaited<ReturnType<UpbitGatewayClient["loadCatalog"]>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [group, setGroup] = useState<QuotationGroupId>("pair");
  const [endpointId, setEndpointId] = useState("");
  const [values, setValues] = useState<Record<string, ParameterValue | undefined>>({});
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [trace, setTrace] = useState<TraceEnvelope | null>(null);
  const [candles, setCandles] = useState<CandleRow[]>([]);
  const [marketOptions, setMarketOptions] = useState<string[]>(["KRW-BTC"]);
  const [isLoading, setLoading] = useState(false);
  const [isTraceOpen, setTraceOpen] = useState(false);
  const [cooldownUntil, setCooldownUntil] = useState(0);
  const [edgeVersion, setEdgeVersion] = useState(0);
  const [candleHasMore, setCandleHasMore] = useState({ past: false, future: false });
  const initialParametersRef = useRef<RequestParameters | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const traceTriggerRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    client.loadCatalog(controller.signal).then(setCatalog).catch((reason: unknown) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "카탈로그를 불러오지 못했습니다.");
    });
    return () => controller.abort();
  }, [client]);

  const endpoints = useMemo(
    () => selectQuotationEndpoints(catalog?.rest_endpoints ?? []),
    [catalog]
  );
  const endpoint = endpoints.find((item) => item.endpoint_id === endpointId)
    ?? endpoints.find((item) => item.functional_group === group)
    ?? null;

  useEffect(() => {
    const first = endpoints.find((item) => item.functional_group === group);
    if (first && endpoint?.functional_group !== group) setEndpointId(first.endpoint_id);
  }, [endpoint?.functional_group, endpoints, group]);

  useEffect(() => {
    if (!endpoint) return;
    setValues(buildInitialParameters(endpoint, context));
    setFieldErrors({});
    setTrace(null);
    setCandles([]);
    setCandleHasMore({ past: false, future: false });
    setEdgeVersion(0);
    initialParametersRef.current = null;
    controllerRef.current?.abort();
    setLoading(false);
  }, [context, endpoint]);

  useEffect(() => {
    if (cooldownUntil <= Date.now()) return;
    const timeoutId = window.setTimeout(() => setCooldownUntil(0), cooldownUntil - Date.now());
    return () => window.clearTimeout(timeoutId);
  }, [cooldownUntil]);

  const execute = async (parameters?: RequestParameters, appendDirection?: "past" | "future") => {
    if (!endpoint || isLoading || Date.now() < cooldownUntil) return;
    const requestParameters = serializeParameters(endpoint, parameters ?? values, context);
    const validationErrors = validateParameterValues(endpoint.parameters, requestParameters);
    if (Object.keys(validationErrors).length > 0) {
      setFieldErrors(validationErrors);
      setError(`입력값을 확인해 주세요: ${Object.values(validationErrors)[0]}`);
      window.setTimeout(() => document.getElementById(
        `quotation-param-${Object.keys(validationErrors)[0].replaceAll(/[^a-zA-Z0-9_-]/g, "-")}`
      )?.focus(), 0);
      return;
    }
    setFieldErrors({});
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setLoading(true);
    setError(null);
    try {
      const nextTrace = await client.execute(endpoint.endpoint_id, requestParameters, controller.signal);
      if (controller.signal.aborted) return;
      setTrace(nextTrace);
      const feedback = describeTraceResponse(nextTrace);
      if (feedback.cooldownMs > 0) setCooldownUntil(Date.now() + feedback.cooldownMs);
      if (feedback.error) return;
      if (endpoint.functional_group === "candle") {
        const page = parseCandleRows(nextTrace.response.body);
        if (appendDirection) {
          const merged = mergeCandleRows(candles, page);
          setCandles(merged);
          if (page.length === 0 || merged.length === candles.length) {
            setCandleHasMore((current) => ({ ...current, [appendDirection]: false }));
          }
        } else {
          setCandles(page);
          setCandleHasMore({
            past: page.length > 0,
            future: page.length > 0 && requestParameters.to !== undefined
          });
          initialParametersRef.current = requestParameters;
        }
      } else if (endpoint.functional_group === "pair") {
        const markets = readStringFields(nextTrace.response.body, "market");
        if (markets.length) {
          setMarketOptions(markets);
          onContextChange(markets.includes(context.market) ? context : marketContext(markets[0]));
        }
      }
    } catch (reason) {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "요청에 실패했습니다.");
    } finally {
      if (!controller.signal.aborted) {
        setLoading(false);
        setEdgeVersion((version) => version + 1);
      }
    }
  };

  const requestCandleEdge = (direction: "past" | "future") => {
    const initial = initialParametersRef.current;
    if (!candleHasMore[direction] || !initial || !endpoint) return;
    if (direction === "future" && initial.to === undefined) {
      setCandleHasMore((current) => ({ ...current, future: false }));
      return;
    }
    const { granularity, unit } = candleInterval(endpoint, initial);
    const next = nextCandleParameters(direction, initial, candles, granularity, unit);
    if (next) void execute(next, direction);
    else setCandleHasMore((current) => ({ ...current, [direction]: false }));
  };

  const closeTrace = () => {
    setTraceOpen(false);
    window.setTimeout(() => traceTriggerRef.current?.focus(), 0);
  };

  if (error && !catalog) return <section className="upbit-workbench-error" role="alert">{error}</section>;
  if (!catalog || !endpoint) return <section className="upbit-workbench-loading" role="status">API 카탈로그를 불러오는 중</section>;

  const activeCount = endpoints.filter((item) => !item.deprecated).length;
  const deprecatedCount = endpoints.length - activeCount;
  const traceFeedback = trace ? describeTraceResponse(trace) : null;
  return (
    <section className="upbit-workbench" aria-label="Quotation API 작업대">
      <header className="upbit-workbench-intro panel">
        <div>
          <p className="eyebrow">공식 카탈로그 {catalog.catalog_version} · {formatKstDate(catalog.verified_at)}</p>
          <h2>Quotation REST API 작업대</h2>
          <p>키와 임의 URL 없이 별도 게이트웨이를 통해 조회하고 마스킹된 추적만 표시합니다.</p>
        </div>
        <strong>활성 {activeCount}개 · 사용 중단 {deprecatedCount}개</strong>
      </header>

      <div className="upbit-workbench-tabs" role="tablist" aria-label="Quotation 기능 그룹">
        {quotationGroups.map((item) => (
          <button key={item.id} role="tab" type="button" aria-selected={group === item.id}
            onClick={() => setGroup(item.id)}>{item.label}</button>
        ))}
      </div>

      <div className="upbit-workbench-grid">
        <section className="upbit-request-builder panel" aria-label="API 요청 작성기">
          <label>API 기능
            <select value={endpoint.endpoint_id} onChange={(event) => {
              const next = endpoints.find((item) => item.endpoint_id === event.target.value);
              if (next) setGroup(next.functional_group as QuotationGroupId);
              setEndpointId(event.target.value);
            }}>
              {quotationGroups.map((tab) => (
                <optgroup key={tab.id} label={tab.label}>
                  {endpoints.filter((item) => item.functional_group === tab.id).map((item) => (
                    <option key={item.endpoint_id} value={item.endpoint_id}>
                      {item.title}{item.deprecated ? " · 사용 중단" : ""}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          </label>
          {endpoint.deprecated ? <p className="upbit-deprecated-badge">사용 중단(deprecated) API · 호환성 확인용</p> : null}
          <WorkbenchCommonSelection context={context} marketOptions={marketOptions} onChange={onContextChange} />
          <div className="upbit-dynamic-fields">
            {endpoint.parameters.filter((parameter) => !isCommonParameter(parameter.name)).map((parameter) => (
              <CatalogParameterField key={parameter.name} idPrefix="quotation-param"
                parameter={parameter} value={values[parameter.name]} error={fieldErrors[parameter.name]}
                endpointId={endpoint.endpoint_id}
                screenInitialMaximum={endpoint.safety === "read" && parameter.name === "count"}
                onChange={(rawValue) => {
                  setError(null);
                  setFieldErrors((current) => ({ ...current, [parameter.name]: "" }));
                  setValues((current) => ({
                    ...current,
                    [parameter.name]: rawValue === undefined
                      ? undefined
                      : coerceParameterValue(parameter, rawValue)
                  }));
                }} />
            ))}
          </div>
          <button className="upbit-execute-button" type="button" disabled={isLoading || Date.now() < cooldownUntil}
            onClick={() => void execute()}>{isLoading ? "요청 중…" : "요청 실행"}</button>
          {error ? <p className="upbit-inline-error" role="alert">{error}</p> : null}
        </section>

        <section className="upbit-result-panel panel" aria-label="API 응답 결과">
          <div className="panel-heading">
            <div><h3>{endpoint.title}</h3><span>{endpoint.method} {endpoint.path}</span></div>
            {trace ? <button ref={traceTriggerRef} className="icon-button" type="button" aria-label="원본 응답과 API 출처 보기"
              onClick={() => setTraceOpen(true)}><FileJson size={18} /></button> : null}
          </div>
          {trace ? (
            <>
              <TraceSummary trace={trace} />
              {traceFeedback?.error ? (
                <p className="upbit-inline-error" role="alert">{traceFeedback.error}</p>
              ) : (
                <ResultRenderer endpoint={endpoint} body={trace.response.body} candles={candles}
                  edgeVersion={edgeVersion} hasMore={candleHasMore} isLoading={isLoading}
                  onRequestEdge={requestCandleEdge} quoteCurrency={context.quote} />
              )}
            </>
          ) : <p className="upbit-empty-result">왼쪽 조건을 확인한 뒤 요청을 실행하세요.</p>}
        </section>
      </div>
      {trace && isTraceOpen ? <TraceDialog trace={trace} endpoint={endpoint} onClose={closeTrace} /> : null}
    </section>
  );
}

export function WorkbenchCommonSelection({ context, marketOptions, onChange }: {
  context: WorkbenchContext;
  marketOptions: string[];
  onChange: (context: WorkbenchContext) => void;
}) {
  return <fieldset className="upbit-common-selection"><legend>공통 조회 기준</legend>
    <label>마켓<select aria-label="마켓" value={context.quote} onChange={(event) => onChange({ ...context, quote: event.target.value, market: `${event.target.value}-${context.base}` })}>
      {["KRW", "BTC", "USDT"].map((quote) => <option key={quote} value={quote}>{quote}</option>)}
    </select></label>
    <label>기준 자산(Base)<input value={context.base} onChange={(event) => onChange({ ...context, base: event.target.value.toUpperCase(), market: `${context.quote}-${event.target.value.toUpperCase()}` })} /></label>
    <label>거래쌍<input value={context.market} readOnly /></label>
  </fieldset>;
}

function TraceSummary({ trace }: { trace: TraceEnvelope }) {
  return <div className="upbit-trace-summary" aria-label="응답 추적 요약">
    <span>HTTP {trace.response.status_code}</span><span>{trace.duration_ms.toLocaleString("ko-KR")} ms</span>
    <span>{trace.rate_limit.group} 잔여 {trace.rate_limit.remaining_sec ?? "-"}</span>
  </div>;
}

function ResultRenderer({ endpoint, body, candles, edgeVersion, hasMore, isLoading, onRequestEdge, quoteCurrency }: {
  endpoint: CatalogEndpoint;
  body: unknown;
  candles: CandleRow[];
  edgeVersion: number;
  hasMore: { past: boolean; future: boolean };
  isLoading: boolean;
  onRequestEdge: (direction: "past" | "future") => void;
  quoteCurrency: string;
}) {
  if (endpoint.functional_group === "candle") return <div className="upbit-candle-result">
    <UpbitCandleChart candles={candles} indicators={[]} edgeRequestVersion={edgeVersion} onRequestEdge={onRequestEdge} quoteCurrency={quoteCurrency} />
    <p>{candles.length.toLocaleString("ko-KR")}개 캔들 · 가장자리 이동 시 연속 조회</p>
    <div className="upbit-candle-pagination" aria-label="캔들 연속 조회">
      <button type="button" disabled={isLoading || !hasMore.past} onClick={() => onRequestEdge("past")}>과거 데이터 조회</button>
      {!hasMore.past ? <span>과거 데이터 끝</span> : null}
      <button type="button" disabled={isLoading || !hasMore.future} onClick={() => onRequestEdge("future")}>미래 데이터 조회</button>
      {!hasMore.future ? <span>최신 데이터 끝</span> : null}
    </div>
    <RecordTable rows={candles.slice(-10).map((item) => ({ started_at: item.startedAt, open: item.open, high: item.high, low: item.low, close: item.close, volume: item.volume }))} quoteCurrency={quoteCurrency} />
  </div>;
  const rows = records(body);
  if (endpoint.functional_group === "ticker") return <div className="upbit-ticker-cards">{rows.map((row, index) => { const assets = rowMarketAssets(row, quoteCurrency); return <article key={`${String(row.market ?? row.code ?? "ticker")}-${index}`}><strong>{String(row.market ?? row.code ?? "현재가")}</strong><b>{formatMoneyValue(row.trade_price, assets.quote)}</b><span>24H {formatMoneyValue(row.acc_trade_price_24h, assets.quote)}</span></article>; })}</div>;
  if (endpoint.functional_group === "orderbook" && rows.some((row) => Array.isArray(row.orderbook_units))) return <div className="upbit-orderbook-ladder">{rows.flatMap((row, rowIndex) => { const assets = rowMarketAssets(row, quoteCurrency); return (Array.isArray(row.orderbook_units) ? row.orderbook_units : []).map((unit, unitIndex) => isRecord(unit) ? <div key={`${rowIndex}-${unitIndex}`}><span>{formatMoneyValue(unit.ask_price, assets.quote)} / {formatAssetValue(unit.ask_size, assets.base)}</span><strong>{unitIndex + 1}</strong><span>{formatMoneyValue(unit.bid_price, assets.quote)} / {formatAssetValue(unit.bid_size, assets.base)}</span></div> : null); })}</div>;
  return <RecordTable rows={rows} quoteCurrency={quoteCurrency} />;
}

function RecordTable({ rows, quoteCurrency, baseAsset = "" }: { rows: Record<string, unknown>[]; quoteCurrency?: string; baseAsset?: string }) {
  if (!rows.length) return <pre className="upbit-json-fallback">응답 데이터가 없습니다.</pre>;
  const keys = [...new Set(rows.flatMap((row) => Object.keys(row)))].slice(0, 8);
  return <div className="upbit-record-table"><table><thead><tr>{keys.map((key) => <th key={key}>{key}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={index}>{keys.map((key) => <td key={key}>{formatRecordValue(row, key, quoteCurrency, baseAsset)}</td>)}</tr>)}</tbody></table></div>;
}

function TraceDialog({ trace, endpoint, onClose }: { trace: TraceEnvelope; endpoint: CatalogEndpoint; onClose: () => void }) {
  const dialogRef = useRef<HTMLElement | null>(null);
  useEffect(() => { focusableElements(dialogRef.current)[0]?.focus(); }, []);
  return <div className="modal-backdrop"><section ref={dialogRef} className="upbit-trace-dialog" role="dialog" aria-modal="true" aria-label="API 요청 추적"
    tabIndex={-1} onKeyDown={(event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab") return;
      const elements = focusableElements(dialogRef.current);
      const first = elements[0];
      const last = elements.at(-1);
      if (!first || !last) return;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }}>
    <button className="icon-button close-button" type="button" aria-label="닫기" onClick={onClose}><X size={18} /></button>
    <h2>API 요청 추적</h2><p>trace {trace.trace_id}</p>
    <a href={endpoint.source_url} target="_blank" rel="noreferrer">Upbit 공식 문서</a>
    <dl><dt>엔드포인트</dt><dd>{endpoint.endpoint_id}</dd><dt>수신 시각</dt><dd>{formatKstDateTime(trace.received_at)}</dd></dl>
    <h3>요청·원본 응답·요청 제한</h3>
    <pre>{JSON.stringify({ request: trace.request, response: trace.response, rate_limit: trace.rate_limit }, null, 2)}</pre>
  </section></div>;
}

function records(body: unknown): Record<string, unknown>[] {
  if (Array.isArray(body)) return body.filter(isRecord);
  return isRecord(body) ? [body] : [];
}

function readStringFields(body: unknown, field: string): string[] {
  return records(body).map((row) => row[field]).filter((value): value is string => typeof value === "string");
}

function marketContext(market: string): WorkbenchContext {
  const [quote = "KRW", base = "BTC"] = market.toUpperCase().split("-");
  return { market: market.toUpperCase(), quote, base };
}

function rowMarketAssets(row: Record<string, unknown>, fallbackQuote = ""): { quote: string; base: string } {
  const [quote = fallbackQuote, base = ""] = String(row.market ?? row.code ?? "").toUpperCase().split("-");
  return { quote: quote || fallbackQuote, base };
}

function numeric(value: unknown): string | number | null {
  if (typeof value === "number") return value;
  if (typeof value === "string" && value.trim() && Number.isFinite(Number(value))) return value;
  return null;
}

function formatMoneyValue(value: unknown, currency: string): string {
  const number = numeric(value);
  return number === null ? String(value ?? "-") : formatMoney(number, currency);
}

function formatAssetValue(value: unknown, asset: string): string {
  const number = numeric(value);
  return number === null ? String(value ?? "-") : formatAssetAmount(number, asset);
}

function formatRecordValue(row: Record<string, unknown>, key: string, fallbackQuote = "", fallbackBase = ""): string {
  const value = row[key];
  if (value === null || value === undefined) return "-";
  if (typeof value === "object") return JSON.stringify(value);
  const raw = String(value);
  if (/(?:date_time|started_at|created_at|updated_at|received_at|timestamp)$/.test(key) && !Number.isNaN(Date.parse(raw))) {
    return formatKstDateTime(raw.endsWith("Z") || /[+-]\d{2}:\d{2}$/.test(raw) ? raw : `${raw}Z`);
  }
  const assets = rowMarketAssets(row, fallbackQuote);
  const quote = assets.quote || fallbackQuote;
  const base = assets.base || fallbackBase;
  if (/(?:price|open|high|low|close)$/.test(key)) return formatMoneyValue(value, quote);
  if (/(?:volume|size|amount)$/.test(key)) return formatAssetValue(value, base);
  const number = numeric(value);
  return number === null ? raw : formatNumber(number);
}

function candleInterval(endpoint: CatalogEndpoint, parameters: RequestParameters): { granularity: CandleGranularity; unit: number } {
  if (endpoint.endpoint_id.includes("seconds")) return { granularity: "second", unit: 1 };
  if (endpoint.endpoint_id.includes("minutes")) return { granularity: "minute", unit: Number(parameters.unit ?? 1) };
  if (endpoint.endpoint_id.includes("weeks")) return { granularity: "week", unit: 1 };
  if (endpoint.endpoint_id.includes("months")) return { granularity: "month", unit: 1 };
  if (endpoint.endpoint_id.includes("years")) return { granularity: "year", unit: 1 };
  return { granularity: "day", unit: 1 };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function focusableElements(container: HTMLElement | null): HTMLElement[] {
  if (!container) return [];
  return Array.from(container.querySelectorAll<HTMLElement>(
    "button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])"
  ));
}
