import { useEffect, useMemo, useRef, useState } from "react";
import { FileJson } from "lucide-react";
import { parameterDisplayName } from "../../components/upbit-api-test/parameterPresentation";
import { formatAssetAmount, formatKstDateTime, formatMoney } from "../../displayFormat";
import { appendBoundedFrame, defaultGatewayWebSocketUrl, framePayloads, isGatewayFrame, marketOptions, streamForTab, workbenchCandleUnits } from "./protocol";
import type { BrowserSocket, CandleUnit, GatewayEvent, GatewayFrameEvent, MarketLike, SocketFactory, UpbitFormat, Visibility, WorkbenchTab } from "./types";
import "./styles.css";

const tabs: { id: WorkbenchTab; label: string }[] = [
  { id: "ticker", label: "현재가" },
  { id: "trade", label: "체결" },
  { id: "orderbook", label: "호가" },
  { id: "candle", label: "캔들" },
  { id: "asset", label: "내 자산" },
  { id: "order", label: "내 주문" }
];
const formats: UpbitFormat[] = ["DEFAULT", "SIMPLE", "JSON_LIST", "SIMPLE_LIST"];
type ChannelState = {
  state: string;
  paused: boolean;
  frames: GatewayFrameEvent[];
  notice: string;
};

function newChannel(): ChannelState {
  return { state: "closed", paused: false, frames: [], notice: "연결 대기 중" };
}

export function UpbitWebSocketWorkbench({
  gatewayUrl,
  markets = [{ market: "KRW-BTC", koreanName: "비트코인" }],
  marketCode,
  onMarketCodeChange,
  showMarketSelection = true,
  socketFactory = (url) => new WebSocket(url) as unknown as BrowserSocket
}: {
  gatewayUrl?: string;
  markets?: MarketLike[];
  marketCode?: string;
  onMarketCodeChange?: (marketCode: string) => void;
  showMarketSelection?: boolean;
  socketFactory?: SocketFactory;
}) {
  const [tab, setTab] = useState<WorkbenchTab>("ticker");
  const [internalMarket, setInternalMarket] = useState("KRW-BTC");
  const [candleUnit, setCandleUnit] = useState<CandleUnit>("1s");
  const [format, setFormat] = useState<UpbitFormat>("DEFAULT");
  const [snapshotOnly, setSnapshotOnly] = useState(false);
  const [realtimeOnly, setRealtimeOnly] = useState(false);
  const [orderbookLevel, setOrderbookLevel] = useState(0);
  const [channels, setChannels] = useState<Record<Visibility, ChannelState>>({
    public: newChannel(),
    private: newChannel()
  });
  const [rawOpen, setRawOpen] = useState(false);
  const socketRefs = useRef<Record<Visibility, BrowserSocket | null>>({ public: null, private: null });
  const ticketRefs = useRef<Record<Visibility, string>>({ public: "", private: "" });
  const rawTriggerRef = useRef<HTMLButtonElement | null>(null);
  const requestSequence = useRef(0);
  const options = useMemo(() => marketOptions(markets), [markets]);
  const market = marketCode?.trim().toUpperCase() || internalMarket;
  const displayOptions = options.some((option) => option.value === market)
    ? options
    : [{ value: market, label: market }, ...options];
  const stream = streamForTab(tab, candleUnit);
  const activeChannel = channels[stream.visibility];
  const { paused, frames, notice } = activeChannel;

  useEffect(() => () => {
    socketRefs.current.public?.close();
    socketRefs.current.private?.close();
  }, []);

  function updateChannel(
    visibility: Visibility,
    update: Partial<ChannelState> | ((channel: ChannelState) => ChannelState)
  ) {
    setChannels((current) => ({
      ...current,
      [visibility]: typeof update === "function"
        ? update(current[visibility])
        : { ...current[visibility], ...update }
    }));
  }

  function requestId(prefix: string) {
    requestSequence.current += 1;
    return `${prefix}-${requestSequence.current}`;
  }

  function selectTab(nextTab: WorkbenchTab) {
    setTab(nextTab);
    const nextStream = streamForTab(nextTab, candleUnit);
    const label = tabs.find((item) => item.id === nextTab)?.label ?? nextTab;
    updateChannel(nextStream.visibility, { notice: `${label} 스트림 선택` });
  }

  function moveTab(event: React.KeyboardEvent<HTMLButtonElement>, currentTab: WorkbenchTab) {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const currentIndex = tabs.findIndex((item) => item.id === currentTab);
    const nextIndex = event.key === "Home"
      ? 0
      : event.key === "End"
        ? tabs.length - 1
        : (currentIndex + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    const nextTab = tabs[nextIndex].id;
    selectTab(nextTab);
    document.getElementById(`upbit-ws-tab-${nextTab}`)?.focus();
  }

  function closeRaw() {
    rawTriggerRef.current?.focus();
    setRawOpen(false);
  }

  function handleRawDialogKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      closeRaw();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = Array.from(event.currentTarget.querySelectorAll<HTMLElement>(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
    ));
    const first = focusable[0];
    const last = focusable.at(-1);
    if (!first || !last) return;
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function websocketUrl() {
    if (gatewayUrl) return gatewayUrl;
    return defaultGatewayWebSocketUrl(window.location);
  }

  function connect() {
    const visibility = stream.visibility;
    socketRefs.current[visibility]?.close();
    updateChannel(visibility, { state: "connecting", notice: "게이트웨이에 연결 중" });
    const socket = socketFactory(websocketUrl());
    socketRefs.current[visibility] = socket;
    ticketRefs.current[visibility] = globalThis.crypto?.randomUUID?.() ?? `ticket-${Date.now()}`;
    const onOpen = () => {
      socket.send(JSON.stringify({
        action: "connect",
        request_id: requestId("connect"),
        visibility,
        ticket: ticketRefs.current[visibility],
        format
      }));
    };
    const onMessage = (event: Event) => {
      try {
        const gatewayEvent = JSON.parse((event as MessageEvent<string>).data) as GatewayEvent;
        if (isGatewayFrame(gatewayEvent)) updateChannel(visibility, (channel) => ({
          ...channel,
          frames: appendBoundedFrame(channel.frames, gatewayEvent)
        }));
        if (gatewayEvent.event === "connection" && gatewayEvent.state) {
          updateChannel(visibility, {
            state: gatewayEvent.state,
            paused: gatewayEvent.state === "paused",
            notice: `연결 상태: ${gatewayEvent.state}`
          });
        }
        if (gatewayEvent.event === "subscription") updateChannel(visibility, { notice: `구독 제어: ${gatewayEvent.action ?? "완료"}` });
        if (gatewayEvent.event === "error") updateChannel(visibility, { notice: `${gatewayEvent.code ?? "ERROR"}: ${gatewayEvent.message ?? "오류"}` });
      } catch {
        updateChannel(visibility, { notice: "게이트웨이 메시지 형식이 올바르지 않습니다." });
      }
    };
    const onClose = () => {
      if (socketRefs.current[visibility] === socket) {
        updateChannel(visibility, { state: "closed", paused: false, notice: "연결이 종료되었습니다." });
      }
    };
    socket.addEventListener("open", onOpen);
    socket.addEventListener("message", onMessage);
    socket.addEventListener("close", onClose);
  }

  function send(action: string, extra: Record<string, unknown> = {}) {
    const visibility = stream.visibility;
    const socket = socketRefs.current[visibility];
    if (!socket || socket.readyState !== 1) {
      updateChannel(visibility, { notice: "먼저 연결해 주세요." });
      return;
    }
    socket.send(JSON.stringify({ action, request_id: requestId(action), ...extra }));
  }

  function disconnect() {
    const visibility = stream.visibility;
    const socket = socketRefs.current[visibility];
    socketRefs.current[visibility] = null;
    socket?.close();
    updateChannel(visibility, { state: "closed", paused: false, notice: "연결을 해제했습니다." });
  }

  function clearFrames() {
    updateChannel(stream.visibility, { frames: [], notice: "프레임을 지웠습니다." });
  }

  function parameters() {
    if (tab === "asset") return {};
    const value: Record<string, unknown> = { codes: [market] };
    if (tab === "order") return value;
    if (snapshotOnly) value.is_only_snapshot = true;
    if (realtimeOnly) value.is_only_realtime = true;
    if (tab === "orderbook" && orderbookLevel > 0) value.level = orderbookLevel;
    return value;
  }

  const payloads = frames.flatMap(framePayloads);
  const lastPayload = payloads.at(-1);

  return <section className="upbit-ws" aria-label="업비트 웹소켓 작업대">
    <header>
      <div><p className="eyebrow">UPBIT WEBSOCKET GATEWAY</p><h1>실시간 API 작업대</h1></div>
      <div className="status-group" aria-label="웹소켓 연결 상태">
        <span><small>공개</small><b className={`status status-${channels.public.state}`} aria-label="공개 연결 상태">{channels.public.state}</b></span>
        <span><small>비공개</small><b className={`status status-${channels.private.state}`} aria-label="비공개 연결 상태">{channels.private.state}</b></span>
      </div>
    </header>
    <nav role="tablist" aria-label="웹소켓 데이터 그룹">
      {tabs.map((item) => <button key={item.id} id={`upbit-ws-tab-${item.id}`} role="tab" aria-controls={`upbit-ws-panel-${item.id}`} tabIndex={tab === item.id ? 0 : -1} aria-selected={tab === item.id} onKeyDown={(event) => moveTab(event, item.id)} onClick={() => selectTab(item.id)}>{item.label}</button>)}
    </nav>
    <div className="controls" aria-label="웹소켓 구독 조건">
      {showMarketSelection && tab !== "asset" && <label>페어<select aria-label="페어" value={market} onChange={(event) => {
        setInternalMarket(event.target.value);
        onMarketCodeChange?.(event.target.value);
      }}>
        {displayOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select></label>}
      {tab === "candle" && <label>캔들 주기<select aria-label="캔들 주기" value={candleUnit} onChange={(event) => setCandleUnit(event.target.value as CandleUnit)}>
        {workbenchCandleUnits.map((unit) => <option key={unit}>{unit}</option>)}
      </select></label>}
      <label>응답 포맷<select aria-label="응답 포맷" value={format} onChange={(event) => setFormat(event.target.value as UpbitFormat)}>
        {formats.map((item) => <option key={item}>{item}</option>)}
      </select></label>
      {stream.visibility === "public" && <><label className="check"><input type="checkbox" checked={snapshotOnly} onChange={(event) => { setSnapshotOnly(event.target.checked); if (event.target.checked) setRealtimeOnly(false); }} />{parameterDisplayName(stream.endpointId, "is_only_snapshot")}</label>
      <label className="check"><input type="checkbox" checked={realtimeOnly} onChange={(event) => { setRealtimeOnly(event.target.checked); if (event.target.checked) setSnapshotOnly(false); }} />{parameterDisplayName(stream.endpointId, "is_only_realtime")}</label></>}
      {tab === "orderbook" && <label>{parameterDisplayName(stream.endpointId, "level")}<input aria-label={parameterDisplayName(stream.endpointId, "level")} type="number" min="0" value={orderbookLevel} onChange={(event) => setOrderbookLevel(Number(event.target.value))} /></label>}
    </div>
    <div className="actions">
      <button onClick={connect}>연결</button>
      <button onClick={() => send("subscribe", { endpoint_id: stream.endpointId, parameters: parameters() })}>구독</button>
      <button onClick={() => send("pause", { paused: !paused })}>{paused ? "다시 시작" : "일시 정지"}</button>
      <button onClick={() => send("list")}>목록 조회</button>
      <button onClick={() => send("reconnect")}>재연결</button>
      <button onClick={() => send("unsubscribe", { endpoint_id: stream.endpointId })}>구독 해제</button>
      <button onClick={disconnect}>연결 해제</button>
      <button onClick={clearFrames}>프레임 지우기</button>
      <button ref={rawTriggerRef} className="trace-icon-button" type="button"
        aria-label="raw 추적" aria-haspopup="dialog" onClick={() => setRawOpen(true)}>
        <FileJson size={18} aria-hidden="true" />
      </button>
    </div>
    <p className="notice" role="status">{notice}</p>
    <div role="tabpanel" id={`upbit-ws-panel-${tab}`} aria-labelledby={`upbit-ws-tab-${tab}`}>
      <LiveVisualization tab={tab} payload={lastPayload} payloads={payloads} />
    </div>
    {rawOpen && <div className="raw-dialog" role="dialog" aria-label="raw frame 추적" aria-modal="true" tabIndex={-1} onKeyDown={handleRawDialogKeyDown}>
      <div className="raw-dialog-head"><h2>최근 raw frame ({frames.length}/200)</h2><button autoFocus onClick={closeRaw}>닫기</button></div>
      <ol>{frames.slice().reverse().map((frame) => <li key={`${frame.connection_id}-${frame.sequence}`}>
        <strong>#{frame.sequence} · {frame.trace_id}</strong><small>{formatKstDateTime(frame.received_at)} · {frame.binary ? "binary" : "text"}</small><small>{frame.provenance.visibility} · {frame.provenance.format} · {frame.provenance.endpoint_ids.join(", ")}</small><pre tabIndex={0} aria-label={`raw frame ${frame.sequence}`}>{frame.raw}</pre>
      </li>)}</ol>
    </div>}
  </section>;
}

function numericValue(value: unknown): string | number | null {
  if (typeof value === "number") return value;
  if (typeof value === "string" && value.trim() !== "" && Number.isFinite(Number(value))) {
    return value;
  }
  return null;
}

function marketAssets(code: unknown): { quote: string; base: string } {
  const [quote = "", base = ""] = String(code ?? "").toUpperCase().split("-");
  return { quote, base };
}

function money(value: unknown, currency: string) {
  const numeric = numericValue(value);
  return numeric === null ? "—" : formatMoney(numeric, currency);
}

function amount(value: unknown, asset: string) {
  const numeric = numericValue(value);
  return numeric === null ? "—" : formatAssetAmount(numeric, asset);
}

function LiveVisualization({ tab, payload, payloads }: { tab: WorkbenchTab; payload?: Record<string, unknown>; payloads: Record<string, unknown>[] }) {
  if (!payload) return <div className="empty">구독하면 실시간 데이터가 이곳에 표시됩니다.</div>;
  if (tab === "ticker") {
    const code = payload.code ?? payload.cd;
    return <article aria-label="실시간 현재가" className="visual ticker"><span>{String(code ?? "")}</span><strong>{money(payload.trade_price ?? payload.tp, marketAssets(code).quote)}</strong><small>{String(payload.change ?? payload.c ?? "")}</small></article>;
  }
  if (tab === "trade") return <article aria-label="실시간 체결" className="visual"><h2>최근 체결</h2><ol>{payloads.slice(-20).reverse().map((item, index) => { const code = item.code ?? item.cd; const assets = marketAssets(code); return <li key={index}><b>{String(code ?? "")}</b><span>{money(item.trade_price ?? item.tp, assets.quote)}</span><span>{amount(item.trade_volume ?? item.tv, assets.base)}</span></li>; })}</ol></article>;
  if (tab === "orderbook") {
    const units = (payload.orderbook_units ?? payload.obu ?? []) as Record<string, unknown>[];
    const assets = marketAssets(payload.code ?? payload.cd);
    return <article aria-label="실시간 호가" className="visual"><h2>호가</h2><table><thead><tr><th>매도</th><th>가격</th><th>매수</th></tr></thead><tbody>{units.slice(0, 15).map((unit, index) => <tr key={index}><td>{amount(unit.ask_size ?? unit.as, assets.base)}</td><td>{money(unit.ask_price ?? unit.ap, assets.quote)} / {money(unit.bid_price ?? unit.bp, assets.quote)}</td><td>{amount(unit.bid_size ?? unit.bs, assets.base)}</td></tr>)}</tbody></table></article>;
  }
  if (tab === "candle") { const code = payload.code ?? payload.cd; const quote = marketAssets(code).quote; return <article aria-label="실시간 캔들" className="visual candle"><h2>{String(code ?? "")} 캔들</h2><div>{[["시가", payload.opening_price ?? payload.op], ["고가", payload.high_price ?? payload.hp], ["저가", payload.low_price ?? payload.lp], ["종가", payload.trade_price ?? payload.tp]].map(([label, value]) => <span key={String(label)}><small>{String(label)}</small><b>{money(value, quote)}</b></span>)}</div></article>; }
  if (tab === "asset") {
    const assets = (payload.assets ?? payload.ast ?? []) as Record<string, unknown>[];
    return <article aria-label="내 자산 이벤트" className="visual"><h2>내 자산</h2><table><thead><tr><th>자산</th><th>주문 가능</th><th>주문 중</th></tr></thead><tbody>{assets.map((asset, index) => { const currency = String(asset.currency ?? asset.cu ?? ""); return <tr key={`${currency || "asset"}-${index}`}><td>{currency}</td><td>{amount(asset.balance ?? asset.b, currency)}</td><td>{amount(asset.locked ?? asset.l, currency)}</td></tr>; })}</tbody></table></article>;
  }
  const code = payload.code ?? payload.cd;
  const assets = marketAssets(code);
  return <article aria-label="내 주문 이벤트" className="visual"><h2>내 주문</h2><dl><div><dt>페어</dt><dd>{String(code ?? "")}</dd></div><div><dt>상태</dt><dd>{String(payload.state ?? payload.st ?? "")}</dd></div><div><dt>가격</dt><dd>{money(payload.price ?? payload.p, assets.quote)}</dd></div><div><dt>수량</dt><dd>{amount(payload.volume ?? payload.v, assets.base)}</dd></div></dl></article>;
}
