import { useEffect, useRef, useState } from "react";
import { analysisWebSocketUrl } from "./api";
import {
  applyAnalysisMessage,
  initialAnalysisState,
  type AnalysisMessage,
  type AnalysisRangeDays,
  type AnalysisState,
  type AnalysisUnit
} from "./analysisStream";

export function useRealtimeAnalysis(
  instrumentId: number | null,
  unit: AnalysisUnit,
  rangeDays: AnalysisRangeDays
): AnalysisState & { connectionStatus: "connecting" | "live" | "offline" } {
  const [state, setState] = useState<AnalysisState>(initialAnalysisState);
  const [connectionStatus, setConnectionStatus] = useState<"connecting" | "live" | "offline">("offline");
  const socketRef = useRef<WebSocket | null>(null);
  const lastSentSubscriptionRef = useRef<{ socket: WebSocket; key: string } | null>(null);
  const subscriptionRef = useRef({ instrumentId, unit, rangeDays });
  subscriptionRef.current = { instrumentId, unit, rangeDays };
  const hasSubscription = instrumentId !== null;

  useEffect(() => {
    setState(initialAnalysisState);
    const socket = socketRef.current;
    if (instrumentId !== null && socket?.readyState === WebSocket.OPEN) {
      sendSubscriptionIfChanged(
        socket,
        { instrumentId, unit, rangeDays },
        lastSentSubscriptionRef
      );
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
      socket.onopen = () => {
        if (disposed || socketRef.current !== socket) return;
        const subscription = subscriptionRef.current;
        const nextInstrumentId = subscription.instrumentId;
        if (nextInstrumentId !== null) {
          sendSubscriptionIfChanged(
            socket,
            { ...subscription, instrumentId: nextInstrumentId },
            lastSentSubscriptionRef
          );
        }
      };
      socket.onmessage = (event) => {
        if (disposed || socketRef.current !== socket) return;
        const message = JSON.parse(String(event.data)) as AnalysisMessage;
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
      activeSocket?.close();
    };
  }, [hasSubscription]);

  return { ...state, connectionStatus };
}

function sendSubscriptionIfChanged(
  socket: WebSocket,
  subscription: { instrumentId: number; unit: AnalysisUnit; rangeDays: AnalysisRangeDays },
  lastSentSubscriptionRef: {
    current: { socket: WebSocket; key: string } | null;
  }
) {
  const key = `${subscription.instrumentId}:${subscription.unit}:${subscription.rangeDays}`;
  const lastSent = lastSentSubscriptionRef.current;
  if (lastSent?.socket === socket && lastSent.key === key) return;
  sendSubscription(socket, subscription);
  lastSentSubscriptionRef.current = { socket, key };
}

function sendSubscription(
  socket: WebSocket,
  subscription: { instrumentId: number; unit: AnalysisUnit; rangeDays: AnalysisRangeDays }
) {
  socket.send(
    JSON.stringify({
      version: "1",
      type: "analysis.subscribe",
      sentAt: new Date().toISOString(),
      ...subscription
    })
  );
}
