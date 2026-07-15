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
  const subscriptionRef = useRef({ instrumentId, unit, rangeDays });
  subscriptionRef.current = { instrumentId, unit, rangeDays };
  const hasSubscription = instrumentId !== null;

  useEffect(() => {
    setState(initialAnalysisState);
    const socket = socketRef.current;
    if (instrumentId !== null && socket?.readyState === WebSocket.OPEN) {
      sendSubscription(socket, { instrumentId, unit, rangeDays });
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
        const subscription = subscriptionRef.current;
        const nextInstrumentId = subscription.instrumentId;
        if (nextInstrumentId !== null) {
          sendSubscription(socket, { ...subscription, instrumentId: nextInstrumentId });
        }
      };
      socket.onmessage = (event) => {
        const message = JSON.parse(String(event.data)) as AnalysisMessage;
        setConnectionStatus("live");
        setState((previous) => applyAnalysisMessage(previous, message));
      };
      socket.onclose = () => {
        if (socketRef.current === socket) socketRef.current = null;
        if (disposed) return;
        setConnectionStatus("offline");
        retryTimer = setTimeout(connect, 1_000);
      };
      socket.onerror = () => socket.close();
      return socket;
    };
    connect();
    return () => {
      disposed = true;
      if (retryTimer) clearTimeout(retryTimer);
      if (socketRef.current === activeSocket) socketRef.current = null;
      activeSocket?.close();
    };
  }, [hasSubscription]);

  return { ...state, connectionStatus };
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
