import { useEffect, useMemo, useRef, useState } from "react";
import { appendBoundedFrame, framePayloads, isGatewayFrame, marketOptions, streamForTab } from "./protocol";
import type { BrowserSocket, CandleUnit, GatewayEvent, GatewayFrameEvent, MarketLike, SocketFactory, UpbitFormat, WorkbenchTab } from "./types";
import "./styles.css";

const tabs: { id: WorkbenchTab; label: string }[] = [
  { id: "ticker", label: "현재가" },
  { id: "trade", label: "체결" },
  { id: "orderbook", label: "호가" },
  { id: "candle", label: "캔들" },
  { id: "asset", label: "내 자산" },
  { id: "order", label: "내 주문" }
];
const candleUnits: CandleUnit[] = ["1s", "1m", "3m", "5m", "10m", "15m", "30m", "60m", "240m"];
const formats: UpbitFormat[] = ["DEFAULT", "SIMPLE", "JSON_LIST", "SIMPLE_LIST"];

export function UpbitWebSocketWorkbench({
  gatewayUrl,
  markets = [{ market: "KRW-BTC", koreanName: "비트코인" }],
  marketCode,
  onMarketCodeChange,
  socketFactory = (url) => new WebSocket(url) as unknown as BrowserSocket
}: {
  gatewayUrl?: string;
  markets?: MarketLike[];
  marketCode?: string;
  onMarketCodeChange?: (marketCode: string) => void;
  socketFactory?: SocketFactory;
}) {
  const [tab, setTab] = useState<WorkbenchTab>("ticker");
  const [internalMarket, setInternalMarket] = useState("KRW-BTC");
  const [candleUnit, setCandleUnit] = useState<CandleUnit>("1s");
  const [format, setFormat] = useState<UpbitFormat>("DEFAULT");
  const [snapshotOnly, setSnapshotOnly] = useState(false);
  const [realtimeOnly, setRealtimeOnly] = useState(false);
  const [orderbookLevel, setOrderbookLevel] = useState(0);
  const [state, setState] = useState("closed");
  const [paused, setPaused] = useState(false);
  const [frames, setFrames] = useState<GatewayFrameEvent[]>([]);
  const [notice, setNotice] = useState("연결 대기 중");
  const [rawOpen, setRawOpen] = useState(false);
  const socketRef = useRef<BrowserSocket | null>(null);
  const ticketRef = useRef("");
  const requestSequence = useRef(0);
  const options = useMemo(() => marketOptions(markets), [markets]);
  const market = marketCode?.trim().toUpperCase() || internalMarket;
  const displayOptions = options.some((option) => option.value === market)
    ? options
    : [{ value: market, label: market }, ...options];
  const stream = streamForTab(tab, candleUnit);

  useEffect(() => () => socketRef.current?.close(), []);

  function requestId(prefix: string) {
    requestSequence.current += 1;
    return `${prefix}-${requestSequence.current}`;
  }

  function websocketUrl() {
    if (gatewayUrl) return gatewayUrl;
    const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${scheme}//${window.location.host}/api/v1/websocket`;
  }

  function connect() {
    socketRef.current?.close();
    setState("connecting");
    setNotice("게이트웨이에 연결 중");
    const socket = socketFactory(websocketUrl());
    socketRef.current = socket;
    ticketRef.current = globalThis.crypto?.randomUUID?.() ?? `ticket-${Date.now()}`;
    const onOpen = () => {
      socket.send(JSON.stringify({
        action: "connect",
        request_id: requestId("connect"),
        visibility: stream.visibility,
        ticket: ticketRef.current,
        format
      }));
    };
    const onMessage = (event: Event) => {
      try {
        const gatewayEvent = JSON.parse((event as MessageEvent<string>).data) as GatewayEvent;
        if (isGatewayFrame(gatewayEvent)) setFrames((current) => appendBoundedFrame(current, gatewayEvent));
        if (gatewayEvent.event === "connection" && gatewayEvent.state) {
          setState(gatewayEvent.state);
          setPaused(gatewayEvent.state === "paused");
          setNotice(`연결 상태: ${gatewayEvent.state}`);
        }
        if (gatewayEvent.event === "subscription") setNotice(`구독 제어: ${gatewayEvent.action ?? "완료"}`);
        if (gatewayEvent.event === "error") setNotice(`${gatewayEvent.code ?? "ERROR"}: ${gatewayEvent.message ?? "오류"}`);
      } catch {
        setNotice("게이트웨이 메시지 형식이 올바르지 않습니다.");
      }
    };
    const onClose = () => setState("closed");
    socket.addEventListener("open", onOpen);
    socket.addEventListener("message", onMessage);
    socket.addEventListener("close", onClose);
  }

  function send(action: string, extra: Record<string, unknown> = {}) {
    const socket = socketRef.current;
    if (!socket || socket.readyState !== 1) {
      setNotice("먼저 연결해 주세요.");
      return;
    }
    socket.send(JSON.stringify({ action, request_id: requestId(action), ...extra }));
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
      <span className={`status status-${state}`}>{state}</span>
    </header>
    <nav role="tablist" aria-label="웹소켓 데이터 그룹">
      {tabs.map((item) => <button key={item.id} role="tab" aria-selected={tab === item.id} onClick={() => {
        setTab(item.id); setFrames([]); setNotice(`${item.label} 스트림 선택`);
      }}>{item.label}</button>)}
    </nav>
    <div className="controls" aria-label="웹소켓 구독 조건">
      {tab !== "asset" && <label>페어<select aria-label="페어" value={market} onChange={(event) => {
        setInternalMarket(event.target.value);
        onMarketCodeChange?.(event.target.value);
      }}>
        {displayOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select></label>}
      {tab === "candle" && <label>캔들 주기<select aria-label="캔들 주기" value={candleUnit} onChange={(event) => setCandleUnit(event.target.value as CandleUnit)}>
        {candleUnits.map((unit) => <option key={unit}>{unit}</option>)}
      </select></label>}
      <label>응답 포맷<select aria-label="응답 포맷" value={format} onChange={(event) => setFormat(event.target.value as UpbitFormat)}>
        {formats.map((item) => <option key={item}>{item}</option>)}
      </select></label>
      {stream.visibility === "public" && <><label className="check"><input type="checkbox" checked={snapshotOnly} onChange={(event) => { setSnapshotOnly(event.target.checked); if (event.target.checked) setRealtimeOnly(false); }} />스냅샷만</label>
      <label className="check"><input type="checkbox" checked={realtimeOnly} onChange={(event) => { setRealtimeOnly(event.target.checked); if (event.target.checked) setSnapshotOnly(false); }} />실시간만</label></>}
      {tab === "orderbook" && <label>호가 모아보기<input aria-label="호가 모아보기" type="number" min="0" value={orderbookLevel} onChange={(event) => setOrderbookLevel(Number(event.target.value))} /></label>}
    </div>
    <div className="actions">
      <button onClick={connect}>연결</button>
      <button onClick={() => send("subscribe", { endpoint_id: stream.endpointId, parameters: parameters() })}>구독</button>
      <button onClick={() => send("pause", { paused: !paused })}>{paused ? "다시 시작" : "일시 정지"}</button>
      <button onClick={() => send("list")}>목록 조회</button>
      <button onClick={() => send("reconnect")}>재연결</button>
      <button onClick={() => send("unsubscribe", { endpoint_id: stream.endpointId })}>구독 해제</button>
      <button onClick={() => setRawOpen(true)}>raw 추적</button>
    </div>
    <p className="notice" role="status">{notice}</p>
    <LiveVisualization tab={tab} payload={lastPayload} payloads={payloads} />
    {rawOpen && <div className="raw-dialog" role="dialog" aria-label="raw frame 추적" aria-modal="true">
      <div className="raw-dialog-head"><h2>최근 raw frame ({frames.length}/200)</h2><button onClick={() => setRawOpen(false)}>닫기</button></div>
      <ol>{frames.slice().reverse().map((frame) => <li key={`${frame.connection_id}-${frame.sequence}`}>
        <strong>#{frame.sequence} · {frame.trace_id}</strong><small>{frame.received_at} · {frame.binary ? "binary" : "text"}</small><pre>{frame.raw}</pre>
      </li>)}</ol>
    </div>}
  </section>;
}

function number(value: unknown) {
  return typeof value === "number" ? value.toLocaleString("ko-KR") : "—";
}

function LiveVisualization({ tab, payload, payloads }: { tab: WorkbenchTab; payload?: Record<string, unknown>; payloads: Record<string, unknown>[] }) {
  if (!payload) return <div className="empty">구독하면 실시간 데이터가 이곳에 표시됩니다.</div>;
  if (tab === "ticker") return <article aria-label="실시간 현재가" className="visual ticker"><span>{String(payload.code ?? payload.cd ?? "")}</span><strong>{number(payload.trade_price ?? payload.tp)}</strong><small>{String(payload.change ?? payload.c ?? "")}</small></article>;
  if (tab === "trade") return <article aria-label="실시간 체결" className="visual"><h2>최근 체결</h2><ol>{payloads.slice(-20).reverse().map((item, index) => <li key={index}><b>{String(item.code ?? item.cd ?? "")}</b><span>{number(item.trade_price ?? item.tp)}</span><span>{number(item.trade_volume ?? item.tv)}</span></li>)}</ol></article>;
  if (tab === "orderbook") {
    const units = (payload.orderbook_units ?? payload.obu ?? []) as Record<string, unknown>[];
    return <article aria-label="실시간 호가" className="visual"><h2>호가</h2><table><thead><tr><th>매도</th><th>가격</th><th>매수</th></tr></thead><tbody>{units.slice(0, 15).map((unit, index) => <tr key={index}><td>{number(unit.ask_size ?? unit.as)}</td><td>{number(unit.ask_price ?? unit.ap)} / {number(unit.bid_price ?? unit.bp)}</td><td>{number(unit.bid_size ?? unit.bs)}</td></tr>)}</tbody></table></article>;
  }
  if (tab === "candle") return <article aria-label="실시간 캔들" className="visual candle"><h2>{String(payload.code ?? payload.cd ?? "")} 캔들</h2><div>{[["시가", payload.opening_price ?? payload.op], ["고가", payload.high_price ?? payload.hp], ["저가", payload.low_price ?? payload.lp], ["종가", payload.trade_price ?? payload.tp]].map(([label, value]) => <span key={String(label)}><small>{String(label)}</small><b>{number(value)}</b></span>)}</div></article>;
  return <article aria-label={tab === "asset" ? "내 자산 이벤트" : "내 주문 이벤트"} className="visual"><h2>{tab === "asset" ? "내 자산" : "내 주문"}</h2><pre>{JSON.stringify(payload, null, 2)}</pre></article>;
}
