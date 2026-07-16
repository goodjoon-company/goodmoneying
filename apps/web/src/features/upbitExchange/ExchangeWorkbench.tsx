import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { FileJson } from "lucide-react";
import { formatAssetAmount, formatKstDateTime, formatMoney, formatNumber } from "../../displayFormat";
import { CatalogParameterField } from "../../components/upbit-api-test/CatalogParameterField";
import {
  coerceParameterInputValue,
  initialParameterInputValues,
  validateParameterValues
} from "../../components/upbit-api-test/parameterInput";
import { friendlyGatewayError } from "./gateway";
import type {
  CatalogParameter,
  ExchangeCatalogEndpoint,
  ExchangeFunctionalGroup,
  ExchangeGateway,
  ExchangeMarketConceptAdapter,
  TraceEnvelope
} from "./types";
import "./upbit-exchange.css";

const groups: Array<{ id: ExchangeFunctionalGroup; label: string }> = [
  { id: "pocket", label: "포켓" },
  { id: "asset", label: "계정" },
  { id: "order", label: "주문" },
  { id: "withdrawal", label: "출금" },
  { id: "deposit", label: "입금" },
  { id: "travel_rule", label: "Travel Rule" },
  { id: "service", label: "서비스" }
];

const defaultMarketAdapter: ExchangeMarketConceptAdapter = {
  normalize: (value) => value.trim().toUpperCase(),
  suggestions: ["KRW-BTC", "KRW-ETH"],
  inputLabel: "거래쌍(market)"
};

export type ExchangeWorkbenchProps = {
  gateway: ExchangeGateway;
  initialGroup?: ExchangeFunctionalGroup;
  marketAdapter?: ExchangeMarketConceptAdapter;
  marketValue?: string;
  onMarketChange?: (market: string) => void;
  onTraceOpen?: (trace: TraceEnvelope) => void;
  showMarketSelection?: boolean;
};

export type ExchangeWorkbenchExtensionProps = Pick<
  ExchangeWorkbenchProps,
  "gateway" | "marketAdapter" | "marketValue" | "onMarketChange" | "onTraceOpen"
>;

export function ExchangeWorkbench({
  gateway,
  initialGroup = "pocket",
  marketAdapter = defaultMarketAdapter,
  marketValue = "",
  onMarketChange,
  onTraceOpen,
  showMarketSelection = true
}: ExchangeWorkbenchProps) {
  const [activeGroup, setActiveGroup] = useState(initialGroup);
  const [endpoints, setEndpoints] = useState<ExchangeCatalogEndpoint[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [credentialConfigured, setCredentialConfigured] = useState<boolean | null>(null);
  const [values, setValues] = useState<Record<string, string | boolean>>({});
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [sharedMarket, setSharedMarket] = useState(() => marketAdapter.normalize(marketValue));
  const [trace, setTrace] = useState<TraceEnvelope | null>(null);
  const [traceOpen, setTraceOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [executing, setExecuting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestGenerationRef = useRef(0);
  const traceTriggerRef = useRef<HTMLButtonElement>(null);
  const traceCloseRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([gateway.getHealth(), gateway.getCatalog()]).then(
      ([health, catalog]) => {
        if (cancelled) return;
        const exchangeEndpoints = catalog.rest_endpoints.filter(
          (endpoint) => endpoint.category === "exchange"
        );
        setCredentialConfigured(health.credentials_configured);
        setEndpoints(exchangeEndpoints);
        const initialEndpoint = exchangeEndpoints.find(
          (endpoint) => endpoint.functional_group === initialGroup
        );
        setSelectedId(initialEndpoint?.endpoint_id ?? null);
        setValues(initialParameterInputValues(initialEndpoint?.parameters ?? []));
        setLoading(false);
      },
      (caught: unknown) => {
        if (cancelled) return;
        setError(errorMessage(caught));
        setLoading(false);
      }
    );
    return () => { cancelled = true; };
  }, [gateway, initialGroup]);

  useEffect(() => {
    setSharedMarket(marketAdapter.normalize(marketValue));
  }, [marketAdapter, marketValue]);

  useEffect(() => {
    if (!traceOpen) return;
    traceCloseRef.current?.focus();
    const handleDialogKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeTrace();
      if (event.key === "Tab") {
        event.preventDefault();
        traceCloseRef.current?.focus();
      }
    };
    document.addEventListener("keydown", handleDialogKeyDown);
    return () => document.removeEventListener("keydown", handleDialogKeyDown);
  }, [traceOpen]);

  const visibleEndpoints = useMemo(
    () => endpoints.filter((endpoint) => endpoint.functional_group === activeGroup),
    [activeGroup, endpoints]
  );
  const selected = endpoints.find((endpoint) => endpoint.endpoint_id === selectedId) ?? null;

  const chooseGroup = (group: ExchangeFunctionalGroup) => {
    const next = endpoints.find((endpoint) => endpoint.functional_group === group);
    setActiveGroup(group);
    setSelectedId(next?.endpoint_id ?? null);
    resetRequestState(next?.parameters);
  };
  const chooseEndpoint = (endpoint: ExchangeCatalogEndpoint) => {
    setSelectedId(endpoint.endpoint_id);
    resetRequestState(endpoint.parameters);
  };
  const handleTabKeyDown = (
    event: ReactKeyboardEvent<HTMLButtonElement>,
    currentGroup: ExchangeFunctionalGroup
  ) => {
    const currentIndex = groups.findIndex((group) => group.id === currentGroup);
    let nextIndex: number | null = null;
    if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % groups.length;
    if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + groups.length) % groups.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = groups.length - 1;
    if (nextIndex === null) return;
    event.preventDefault();
    chooseGroup(groups[nextIndex].id);
    const tabs = event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]');
    tabs?.[nextIndex]?.focus();
  };
  const resetRequestState = (parameters: CatalogParameter[] = []) => {
    requestGenerationRef.current += 1;
    setValues(initialParameterInputValues(parameters));
    setFieldErrors({});
    setTrace(null);
    setTraceOpen(false);
    setError(null);
    setExecuting(false);
  };

  const execute = async () => {
    if (!selected || selected.safety === "blocked") return;
    const parameters = toGatewayParameters(
      selected.parameters,
      { ...values, market: sharedMarket },
      marketAdapter
    );
    const validationErrors = validateParameterValues(selected.parameters, parameters);
    if (Object.keys(validationErrors).length > 0) {
      setTrace(null);
      setFieldErrors(validationErrors);
      setError(`입력값을 확인해 주세요: ${Object.values(validationErrors)[0]}`);
      window.setTimeout(() => document.getElementById(
        `exchange-param-${Object.keys(validationErrors)[0].replaceAll(/[^a-zA-Z0-9_-]/g, "-")}`
      )?.focus(), 0);
      return;
    }
    setFieldErrors({});
    if (!hasRequiredAlternative(selected.any_of_required, parameters)) {
      setTrace(null);
      setError(`필수 입력 조합을 입력해 주세요: ${formatRequiredAlternatives(selected.any_of_required)}`);
      return;
    }
    const conflictingParameters = findMutuallyExclusiveGroup(
      selected.mutually_exclusive,
      parameters
    );
    if (conflictingParameters) {
      const message = `파라미터를 동시에 사용할 수 없습니다: ${conflictingParameters.join(", ")}`;
      setTrace(null);
      setFieldErrors(Object.fromEntries(
        conflictingParameters.map((name) => [name, "다른 상호 배타 파라미터와 함께 입력할 수 없습니다."])
      ));
      setError(message);
      window.setTimeout(() => document.getElementById(
        `exchange-param-${conflictingParameters[0].replaceAll(/[^a-zA-Z0-9_-]/g, "-")}`
      )?.focus(), 0);
      return;
    }
    const requestGeneration = ++requestGenerationRef.current;
    const requestedEndpointId = selected.endpoint_id;
    setExecuting(true);
    setError(null);
    try {
      const nextTrace = await gateway.execute({
        endpoint_id: requestedEndpointId,
        parameters
      });
      if (requestGenerationRef.current !== requestGeneration) return;
      if (nextTrace.endpoint_id !== requestedEndpointId) {
        setTrace(null);
        setError("응답 출처가 선택한 API 기능과 일치하지 않습니다.");
        return;
      }
      setTrace(nextTrace);
      setError(nextTrace.response.status_code >= 400
        ? friendlyGatewayError(nextTrace.response.status_code)
        : null);
    } catch (caught) {
      if (requestGenerationRef.current !== requestGeneration) return;
      setTrace(null);
      setError(errorMessage(caught));
    } finally {
      if (requestGenerationRef.current === requestGeneration) setExecuting(false);
    }
  };

  const openTrace = () => {
    if (!trace) return;
    onTraceOpen?.(trace);
    setTraceOpen(true);
  };
  const closeTrace = () => {
    setTraceOpen(false);
    traceTriggerRef.current?.focus();
  };

  return (
    <main className="exchange-workbench" aria-label="Exchange API 작업대">
      <header className="exchange-workbench__header">
        <div>
          <p className="exchange-workbench__eyebrow">UPBIT DEVELOPER WORKBENCH</p>
          <h1>Exchange API</h1>
          <p>조회와 공식 주문 테스트만 실행하고 자산을 바꾸는 요청은 로컬에서 차단합니다.</p>
        </div>
        <output
          className={`credential-state ${credentialConfigured ? "is-ready" : "is-absent"}`}
          aria-label="자격 증명 상태"
          role="status"
        >
          <span aria-hidden="true" />
          {credentialConfigured === null
            ? "서버 확인 중"
            : credentialConfigured
              ? "서버 설정됨"
              : "서버 미설정"}
        </output>
      </header>

      <div className="exchange-tabs" role="tablist" aria-label="Exchange API 기능 그룹">
        {groups.map((group) => {
          const count = endpoints.filter((endpoint) => endpoint.functional_group === group.id).length;
          return (
            <button
              type="button"
              role="tab"
              id={`exchange-tab-${group.id}`}
              aria-controls={`exchange-panel-${group.id}`}
              aria-selected={activeGroup === group.id}
              tabIndex={activeGroup === group.id ? 0 : -1}
              key={group.id}
              onClick={() => chooseGroup(group.id)}
              onKeyDown={(event) => handleTabKeyDown(event, group.id)}
            >
              {group.label} <span>{count}</span>
            </button>
          );
        })}
      </div>

      {loading ? <p role="status">카탈로그를 불러오는 중입니다.</p> : null}
      <div className="exchange-workbench__grid">
        <section className="exchange-request" aria-label="요청 구성">
          {groups.filter((group) => group.id !== activeGroup).map((group) => (
            <div
              key={group.id}
              id={`exchange-panel-${group.id}`}
              role="tabpanel"
              aria-labelledby={`exchange-tab-${group.id}`}
              hidden
            />
          ))}
          <div
            id={`exchange-panel-${activeGroup}`}
            role="tabpanel"
            aria-labelledby={`exchange-tab-${activeGroup}`}
          >
          <nav aria-label="Exchange API 기능 목록" className="exchange-endpoints">
            {visibleEndpoints.map((endpoint) => (
              <button
                type="button"
                key={endpoint.endpoint_id}
                data-endpoint-id={endpoint.endpoint_id}
                className={selectedId === endpoint.endpoint_id ? "is-selected" : ""}
                aria-label={`${endpoint.title} 기능 선택`}
                onClick={() => chooseEndpoint(endpoint)}
              >
                <span>{endpoint.title}</span>
                <small className={`safety safety--${endpoint.safety}`}>{safetyLabel(endpoint.safety)}</small>
              </button>
            ))}
          </nav>

          {selected ? (
            <div className="exchange-form">
              <div className="exchange-form__title">
                <div>
                  <h2>{selected.title}</h2>
                  <code>{selected.method} {selected.path}</code>
                </div>
                {selected.safety === "test" ? <strong className="test-badge">비파괴 테스트</strong> : null}
              </div>
              {selected.safety === "blocked" ? (
                <div className="danger-banner" role="alert">
                  <strong>위험 작업 · 영구 차단</strong>
                  <span>폼과 계약 미리보기만 제공하며 업비트로 전송하지 않습니다.</span>
                </div>
              ) : null}
              {selected.any_of_required?.length ? (
                <p className="parameter-requirement" aria-live="polite">
                  {`필수 입력 조합: ${formatRequiredAlternatives(selected.any_of_required)} · ${
                    hasRequiredAlternative(
                      selected.any_of_required,
                      toGatewayParameters(
                        selected.parameters,
                        { ...values, market: sharedMarket },
                        marketAdapter
                      )
                    ) ? "충족" : "미충족"
                  }`}
                </p>
              ) : null}
              <div className="parameter-grid">
                {selected.parameters.length === 0 ? <p>추가 파라미터가 없습니다.</p> : null}
                {selected.parameters.filter((parameter) =>
                  showMarketSelection || parameter.name !== "market"
                ).map((parameter) => (
                  <CatalogParameterField
                    key={parameter.name}
                    idPrefix="exchange-param"
                    endpointId={selected.endpoint_id}
                    parameter={parameter}
                    value={parameter.name === "market" ? sharedMarket : values[parameter.name]}
                    label={parameter.name === "market" ? marketAdapter.inputLabel : undefined}
                    inputLabel={parameter.name === "market" ? marketAdapter.inputLabel : undefined}
                    suggestions={parameter.name === "market" ? marketAdapter.suggestions : undefined}
                    error={fieldErrors[parameter.name]}
                    onChange={(value) => {
                      setError(null);
                      setFieldErrors((current) => ({ ...current, [parameter.name]: "" }));
                      if (parameter.name === "market" && typeof value === "string") {
                        const normalized = marketAdapter.normalize(value);
                        setSharedMarket(normalized);
                        onMarketChange?.(normalized);
                        return;
                      }
                      setValues((current) => {
                        const next = { ...current };
                        if (value === undefined) delete next[parameter.name];
                        else next[parameter.name] = value;
                        return next;
                      });
                    }}
                  />
                ))}
              </div>
              {selected.safety === "blocked" ? (
                <section className="request-preview" aria-label="최종 요청 미리보기">
                  <h3>로컬 요청 계약</h3>
                  <pre>{JSON.stringify({
                    endpoint_id: selected.endpoint_id,
                    parameters: toPreviewParameters(
                      selected.parameters,
                      { ...values, market: sharedMarket },
                      marketAdapter
                    )
                  }, null, 2)}</pre>
                </section>
              ) : null}
              <button
                className="exchange-execute"
                type="button"
                disabled={executing || selected.safety === "blocked"}
                onClick={execute}
              >
                {selected.safety === "blocked"
                  ? "정책으로 전송 차단됨"
                  : selected.safety === "test"
                    ? "주문 테스트 실행"
                    : executing ? "실행 중" : "조회 실행"}
              </button>
            </div>
          ) : null}
          </div>
        </section>

        <section className="exchange-result" aria-label="응답 결과">
          <div className="exchange-result__heading">
            <div><p>VISUAL RESPONSE</p><h2>응답 결과</h2></div>
            {trace ? <button ref={traceTriggerRef} className="trace-icon-button" type="button"
              aria-label="원본 추적 열기" onClick={openTrace}>
              <FileJson size={18} aria-hidden="true" />
            </button> : null}
          </div>
          {error ? <div className="exchange-error" role="alert">{error}</div> : null}
          {!trace && !error ? <p className="exchange-empty">기능을 선택하고 안전한 요청을 실행하세요.</p> : null}
          {trace && selected ? <ResultView endpoint={selected} trace={trace} /> : null}
        </section>
      </div>

      {traceOpen && trace ? (
        <div className="trace-backdrop" role="presentation" onMouseDown={closeTrace}>
          <section
            className="trace-dialog"
            role="dialog"
            aria-modal="true"
            aria-label="API 원본 추적"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <header><div><p>TRACE</p><h2>API 원본 추적</h2></div><button ref={traceCloseRef} type="button" aria-label="원본 추적 닫기" onClick={closeTrace}>×</button></header>
            <pre>{JSON.stringify(trace, null, 2)}</pre>
          </section>
        </div>
      ) : null}
    </main>
  );
}

function ResultView({ endpoint, trace }: { endpoint: ExchangeCatalogEndpoint; trace: TraceEnvelope }) {
  const body = trace.response.body;
  const rows = Array.isArray(body) ? body.filter(isRecord) : isRecord(body) ? [body] : [];
  if (endpoint.functional_group === "asset") return <DataTable label="계정 잔고 결과" rows={rows} />;
  if (endpoint.functional_group === "order") return <DataTable label="주문 결과" rows={rows} />;
  if (endpoint.functional_group === "withdrawal" || endpoint.functional_group === "deposit") {
    return <DataTable label="입출금 결과" rows={rows} />;
  }
  if (endpoint.functional_group === "service") {
    return <div className="status-cards" aria-label="서비스 상태 결과">{rows.map((row, index) => <article key={index}>{Object.entries(row).map(([key, value]) => <p key={key}><span>{key}</span><strong>{displayCell(row, key, value)}</strong></p>)}</article>)}</div>;
  }
  return rows.length > 0 ? <DataTable label="API 결과" rows={rows} /> : <pre>{JSON.stringify(body, null, 2)}</pre>;
}

function DataTable({ label, rows }: { label: string; rows: Record<string, unknown>[] }) {
  const columns = [...new Set(rows.flatMap((row) => Object.keys(row)))];
  if (rows.length === 0) return <p>응답 데이터가 비어 있습니다.</p>;
  return (
    <div className="result-table-scroll">
      <table aria-label={label}>
        <thead><tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr></thead>
        <tbody>{rows.map((row, index) => <tr key={index}>{columns.map((column) => <td key={column}>{displayCell(row, column, row[column])}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}

function toGatewayParameters(
  definitions: CatalogParameter[],
  values: Record<string, string | boolean>,
  marketAdapter: ExchangeMarketConceptAdapter
): Record<string, unknown> {
  const parameters: Record<string, unknown> = {};
  for (const parameter of definitions) {
    const value = values[parameter.name];
    if (value === undefined || value === "") continue;
    if (parameter.name === "market" && typeof value === "string") {
      parameters[parameter.name] = marketAdapter.normalize(value);
    } else {
      parameters[parameter.name] = coerceParameterInputValue(parameter, value);
    }
  }
  return parameters;
}

function toPreviewParameters(
  definitions: CatalogParameter[],
  values: Record<string, string | boolean>,
  marketAdapter: ExchangeMarketConceptAdapter
) {
  return toGatewayParameters(definitions, values, marketAdapter);
}

function hasRequiredAlternative(
  alternatives: string[][] | undefined,
  parameters: Record<string, unknown>
): boolean {
  if (!alternatives?.length) return true;
  return alternatives.some((alternative) => alternative.every((name) => hasParameterValue(parameters[name])));
}

function findMutuallyExclusiveGroup(
  groups: string[][] | undefined,
  parameters: Record<string, unknown>
): string[] | undefined {
  return groups?.find((group) => (
    group.filter((name) => hasParameterValue(parameters[name])).length > 1
  ));
}

function hasParameterValue(value: unknown): boolean {
  if (value === undefined || value === null || value === "") return false;
  return !Array.isArray(value) || value.length > 0;
}

function formatRequiredAlternatives(alternatives: string[][] | undefined): string {
  return (alternatives ?? []).map((alternative) => alternative.join(" + ")).join(" 또는 ");
}

function errorMessage(caught: unknown): string {
  const status = isRecord(caught) && typeof caught.status === "number" ? caught.status : 500;
  return friendlyGatewayError(status);
}

function safetyLabel(safety: ExchangeCatalogEndpoint["safety"]) {
  return safety === "read" ? "조회" : safety === "test" ? "테스트" : "차단";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function displayCell(row: Record<string, unknown>, column: string, value: unknown) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  const raw = String(value);
  if (/(?:^|_)(?:at|time|timestamp)$/.test(column) && !Number.isNaN(Date.parse(raw))) {
    return formatKstDateTime(raw);
  }
  if (!Number.isFinite(Number(raw)) || raw.trim() === "") return raw;

  const [quote = "", base = ""] = String(row.market ?? "").toUpperCase().split("-");
  const currency = String(row.currency ?? row.unit_currency ?? "").toUpperCase();
  if (/(?:balance|locked|volume|amount)$/.test(column)) {
    return formatAssetAmount(raw, currency || base);
  }
  if (/(?:price|funds|fee)$/.test(column)) {
    return formatMoney(raw, quote || String(row.unit_currency ?? currency));
  }
  return formatNumber(raw);
}
