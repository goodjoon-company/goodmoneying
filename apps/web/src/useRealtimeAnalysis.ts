import { useEffect, useState } from "react";
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

  useEffect(() => {
    setState(initialAnalysisState);
    if (instrumentId === null || typeof WebSocket === "undefined") {
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
      socket.onopen = () => {
        socket.send(
          JSON.stringify({
            version: "1",
            type: "analysis.subscribe",
            sentAt: new Date().toISOString(),
            instrumentId,
            unit,
            rangeDays
          })
        );
      };
      socket.onmessage = (event) => {
        const message = JSON.parse(String(event.data)) as AnalysisMessage;
        setConnectionStatus("live");
        setState((previous) => applyAnalysisMessage(previous, message));
      };
      socket.onclose = () => {
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
      activeSocket?.close();
    };
  }, [instrumentId, rangeDays, unit]);

  return { ...state, connectionStatus };
}
