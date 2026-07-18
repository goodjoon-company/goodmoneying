import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { analysisSnapshotUrl, analysisWebSocketUrl } from "./api";
import {
  applyAnalysisMessage,
  initialAnalysisState,
  type AnalysisMessage,
  type AnalysisMarket,
  type AnalysisRangeDays,
  type AnalysisState,
  type AnalysisUnit
} from "./analysisStream";
import {
  consumeRealtimeEnvelope,
  createRealtimeStreamTracker,
  isRealtimeStreamEnvelope,
  type RealtimeStreamTracker
} from "./realtimeStream";

type SubscriptionGate = {
  socket: WebSocket;
  nextGeneration: number;
  currentGeneration: number;
  acceptedGeneration: number | null;
  pendingGenerations: { generation: number; purpose: "normal" | "recovery" }[];
};

type AnalysisRecoverySnapshot = {
  schema_version: "1.0";
  topic: string;
  scope: string;
  sequence: number;
  cursor: string;
  snapshotVersion: string;
  payload: {
    type: "analysis.snapshot";
    instrument: AnalysisState["instrument"];
    unit: AnalysisUnit;
    candles: AnalysisState["candles"];
    indicatorPoints: AnalysisState["indicators"];
    microstructurePoints: unknown[];
    market: AnalysisMarket;
  };
};

export function useRealtimeAnalysis(
  instrumentId: number | null,
  unit: AnalysisUnit,
  rangeDays: AnalysisRangeDays
): AnalysisState & {
  connectionStatus: "connecting" | "live" | "offline";
  streamRecoveryStatus: "ready" | "snapshot_required";
} {
  const [state, setState] = useState<AnalysisState>(initialAnalysisState);
  const [connectionStatus, setConnectionStatus] = useState<"connecting" | "live" | "offline">("offline");
  const [streamRecoveryStatus, setStreamRecoveryStatus] = useState<"ready" | "snapshot_required">("ready");
  const socketRef = useRef<WebSocket | null>(null);
  const lastSentSubscriptionRef = useRef<{ socket: WebSocket; key: string } | null>(null);
  const subscriptionGateRef = useRef<SubscriptionGate | null>(null);
  const streamTrackerRef = useRef<RealtimeStreamTracker>(createRealtimeStreamTracker());
  const subscriptionRef = useRef({ instrumentId, unit, rangeDays });
  const recoveryGenerationRef = useRef(0);
  subscriptionRef.current = { instrumentId, unit, rangeDays };
  const hasSubscription = instrumentId !== null;

  useEffect(() => {
    if (instrumentId === null) {
      setState(initialAnalysisState);
      setStreamRecoveryStatus("ready");
    }
    const socket = socketRef.current;
    if (instrumentId !== null && isWebSocketOpen(socket)) {
      const sent = sendSubscriptionIfChanged(
        socket,
        { instrumentId, unit, rangeDays },
        lastSentSubscriptionRef
      );
      if (sent) recordSubscriptionGeneration(socket, subscriptionGateRef, "normal");
    }
  }, [instrumentId, rangeDays, unit]);

  useEffect(() => {
    if (!hasSubscription || typeof WebSocket === "undefined") {
      setConnectionStatus("offline");
      return;
    }
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let activeSocket: WebSocket | undefined;
    let disposed = false;
    const connect = () => {
      if (disposed) return;
      setConnectionStatus("connecting");
      const socket = new WebSocket(analysisWebSocketUrl());
      activeSocket = socket;
      socketRef.current = socket;
      subscriptionGateRef.current = createSubscriptionGate(socket);
      streamTrackerRef.current = createRealtimeStreamTracker();
      socket.onopen = () => {
        if (disposed || socketRef.current !== socket) return;
        const subscription = subscriptionRef.current;
        const nextInstrumentId = subscription.instrumentId;
        if (nextInstrumentId !== null) {
          const sent = sendSubscriptionIfChanged(
            socket,
            { ...subscription, instrumentId: nextInstrumentId },
            lastSentSubscriptionRef
          );
          if (sent) recordSubscriptionGeneration(socket, subscriptionGateRef, "normal");
        }
      };
      socket.onmessage = (event) => {
        if (disposed || socketRef.current !== socket) return;
        const parsed = JSON.parse(String(event.data)) as unknown;
        const streamMessage = consumeAnalysisStreamMessage(streamTrackerRef.current, parsed);
        if (streamMessage.kind === "heartbeat" || streamMessage.kind === "ignored") {
          setConnectionStatus("live");
          return;
        }
        if (streamMessage.kind === "snapshot_required") {
          setStreamRecoveryStatus("snapshot_required");
          setConnectionStatus("live");
          setState((previous) => ({ ...previous, error: streamMessage.message }));
          void recoverFromSnapshotRequired(
            socket,
            streamTrackerRef,
            subscriptionGateRef,
            lastSentSubscriptionRef,
            subscriptionRef,
            recoveryGenerationRef,
            setState,
            setStreamRecoveryStatus
          );
          return;
        }
        const message = streamMessage.message;
        if (message.type === "analysis.session") {
          const accepted = acceptSubscriptionGeneration(socket, subscriptionGateRef);
          if (accepted) {
            if (accepted.purpose === "normal") setState(initialAnalysisState);
            setStreamRecoveryStatus("ready");
            setConnectionStatus("live");
          }
          return;
        }
        if (message.type === "analysis.error" && hasPendingGeneration(socket, subscriptionGateRef)) {
          if (!rejectSubscriptionGeneration(socket, subscriptionGateRef)) return;
        } else if (!isCurrentGenerationAccepted(socket, subscriptionGateRef)) {
          return;
        }
        setConnectionStatus("live");
        setState((previous) => applyAnalysisMessage(previous, message));
      };
      socket.onclose = () => {
        if (disposed) return;
        if (socketRef.current !== socket) return;
        socketRef.current = null;
        if (lastSentSubscriptionRef.current?.socket === socket) {
          lastSentSubscriptionRef.current = null;
        }
        if (subscriptionGateRef.current?.socket === socket) {
          subscriptionGateRef.current = null;
        }
        setConnectionStatus("offline");
        retryTimer = setTimeout(connect, 1_000);
      };
      socket.onerror = () => {
        if (!disposed && socketRef.current === socket) socket.close();
      };
      return socket;
    };
    connect();
    return () => {
      disposed = true;
      if (retryTimer) clearTimeout(retryTimer);
      if (socketRef.current === activeSocket) socketRef.current = null;
      if (lastSentSubscriptionRef.current?.socket === activeSocket) {
        lastSentSubscriptionRef.current = null;
      }
      if (subscriptionGateRef.current?.socket === activeSocket) {
        subscriptionGateRef.current = null;
      }
      activeSocket?.close();
    };
  }, [hasSubscription]);

  return { ...state, connectionStatus, streamRecoveryStatus };
}

type AnalysisStreamConsumeResult =
  | { kind: "message"; message: AnalysisMessage }
  | { kind: "heartbeat" }
  | { kind: "snapshot_required"; message: string }
  | { kind: "ignored" };

function consumeAnalysisStreamMessage(
  tracker: RealtimeStreamTracker,
  parsed: unknown
): AnalysisStreamConsumeResult {
  if (!isRealtimeStreamEnvelope(parsed)) {
    return { kind: "message", message: parsed as AnalysisMessage };
  }
  const consumed = consumeRealtimeEnvelope(tracker, parsed);
  if (parsed.message_type === "subscribed") {
    return {
      kind: "message",
      message: {
        type: "analysis.session",
        subscriptionId: String(
          (parsed.payload as Record<string, unknown>).subscriptionId ?? parsed.event_id
        )
      }
    };
  }
  if (consumed.kind === "payload") {
    return { kind: "message", message: consumed.payload as AnalysisMessage };
  }
  if (consumed.kind === "heartbeat") return { kind: "heartbeat" };
  if (consumed.kind === "ignored") return { kind: "ignored" };
  return { kind: "snapshot_required", message: consumed.message };
}

function sendSubscriptionIfChanged(
  socket: WebSocket,
  subscription: { instrumentId: number; unit: AnalysisUnit; rangeDays: AnalysisRangeDays },
  lastSentSubscriptionRef: {
    current: { socket: WebSocket; key: string } | null;
  },
  resumeCursor?: string
): boolean {
  const key = `${subscription.instrumentId}:${subscription.unit}:${subscription.rangeDays}:${resumeCursor ?? ""}`;
  const lastSent = lastSentSubscriptionRef.current;
  if (lastSent?.socket === socket && lastSent.key === key) return false;
  sendSubscription(socket, subscription, resumeCursor);
  lastSentSubscriptionRef.current = { socket, key };
  return true;
}

function isWebSocketOpen(socket: WebSocket | null): socket is WebSocket {
  return typeof WebSocket !== "undefined" && socket?.readyState === WebSocket.OPEN;
}

function createSubscriptionGate(socket: WebSocket): SubscriptionGate {
  return {
    socket,
    nextGeneration: 0,
    currentGeneration: 0,
    acceptedGeneration: null,
    pendingGenerations: []
  };
}

function recordSubscriptionGeneration(
  socket: WebSocket,
  subscriptionGateRef: { current: SubscriptionGate | null },
  purpose: "normal" | "recovery"
) {
  const gate = subscriptionGateRef.current;
  if (gate?.socket !== socket) return;
  gate.nextGeneration += 1;
  gate.currentGeneration = gate.nextGeneration;
  gate.acceptedGeneration = null;
  gate.pendingGenerations.push({ generation: gate.currentGeneration, purpose });
}

function acceptSubscriptionGeneration(
  socket: WebSocket,
  subscriptionGateRef: { current: SubscriptionGate | null }
): { purpose: "normal" | "recovery" } | null {
  const gate = subscriptionGateRef.current;
  if (gate?.socket !== socket) return null;
  const pending = gate.pendingGenerations.shift();
  if (pending === undefined || pending.generation !== gate.currentGeneration) return null;
  gate.acceptedGeneration = pending.generation;
  return { purpose: pending.purpose };
}

function hasPendingGeneration(
  socket: WebSocket,
  subscriptionGateRef: { current: SubscriptionGate | null }
): boolean {
  const gate = subscriptionGateRef.current;
  return gate?.socket === socket && gate.pendingGenerations.length > 0;
}

function rejectSubscriptionGeneration(
  socket: WebSocket,
  subscriptionGateRef: { current: SubscriptionGate | null }
): boolean {
  const gate = subscriptionGateRef.current;
  if (gate?.socket !== socket) return false;
  const pending = gate.pendingGenerations.shift();
  return pending !== undefined && pending.generation === gate.currentGeneration;
}

function isCurrentGenerationAccepted(
  socket: WebSocket,
  subscriptionGateRef: { current: SubscriptionGate | null }
): boolean {
  const gate = subscriptionGateRef.current;
  return (
    gate?.socket === socket &&
    gate.currentGeneration > 0 &&
    gate.acceptedGeneration === gate.currentGeneration
  );
}

function sendSubscription(
  socket: WebSocket,
  subscription: { instrumentId: number; unit: AnalysisUnit; rangeDays: AnalysisRangeDays },
  resumeCursor?: string
) {
  socket.send(
    JSON.stringify({
      version: "1",
      type: "analysis.subscribe",
      schema_version: "1.0",
      message_type: "subscribe",
      topic: `analysis.instrument:${subscription.instrumentId}:${subscription.unit}:${subscription.rangeDays}`,
      scope: "operator:local",
      sentAt: new Date().toISOString(),
      ...subscription,
      ...(resumeCursor ? { resumeCursor } : {}),
      payload: subscription
    })
  );
}

async function recoverFromSnapshotRequired(
  socket: WebSocket,
  streamTrackerRef: { current: RealtimeStreamTracker },
  subscriptionGateRef: { current: SubscriptionGate | null },
  lastSentSubscriptionRef: { current: { socket: WebSocket; key: string } | null },
  subscriptionRef: {
    current: {
      instrumentId: number | null;
      unit: AnalysisUnit;
      rangeDays: AnalysisRangeDays;
    };
  },
  recoveryGenerationRef: { current: number },
  setState: Dispatch<SetStateAction<AnalysisState>>,
  setStreamRecoveryStatus: Dispatch<SetStateAction<"ready" | "snapshot_required">>
) {
  const subscription = subscriptionRef.current;
  if (subscription.instrumentId === null) return;
  const recoveryGeneration = recoveryGenerationRef.current + 1;
  recoveryGenerationRef.current = recoveryGeneration;
  const recoveryKey = `${subscription.instrumentId}:${subscription.unit}:${subscription.rangeDays}`;
  let response: Response;
  try {
    response = await fetch(
      analysisSnapshotUrl({
        instrumentId: subscription.instrumentId,
        unit: subscription.unit,
        rangeDays: subscription.rangeDays
      })
    );
  } catch (error) {
    setState((previous) => ({
      ...previous,
      error: `REST snapshot 복구 실패(${error instanceof Error ? error.message : "unknown"})`
    }));
    return;
  }
  if (!response.ok) {
    setState((previous) => ({ ...previous, error: `REST snapshot 복구 실패(${response.status})` }));
    return;
  }
  const snapshot = (await response.json()) as AnalysisRecoverySnapshot;
  const current = subscriptionRef.current;
  const currentKey =
    current.instrumentId === null
      ? null
      : `${current.instrumentId}:${current.unit}:${current.rangeDays}`;
  if (
    recoveryGenerationRef.current !== recoveryGeneration ||
    currentKey !== recoveryKey ||
    subscriptionGateRef.current?.socket !== socket
  ) {
    return;
  }
  setState({
    instrument: snapshot.payload.instrument,
    candles: snapshot.payload.candles,
    indicators: snapshot.payload.indicatorPoints,
    market: snapshot.payload.market,
    error: null
  });
  setStreamRecoveryStatus("ready");
  const sent = sendSubscriptionIfChanged(
    socket,
    {
      instrumentId: subscription.instrumentId,
      unit: subscription.unit,
      rangeDays: subscription.rangeDays
    },
    lastSentSubscriptionRef,
    snapshot.cursor
  );
  if (sent) recordSubscriptionGeneration(socket, subscriptionGateRef, "recovery");
  streamTrackerRef.current = createRealtimeStreamTracker();
}
