import { useEffect, useState } from "react";
import { systemManagementWebSocketUrl } from "./api";

export type SystemManagementSnapshot = {
  refreshedAt: string;
  realtime: { status: string; statusLabel: string; items: SystemWorkItem[] };
  backfill: { status: string; statusLabel: string; items: SystemWorkItem[] };
  aggregation: {
    id: number; status: string; progressPercent: string; totalTargetCount: number;
    completedTargetCount: number; runningTargetCount: number; failedTargetCount: number;
    items: AggregationWorkItem[];
  } | null;
};

type SystemWorkItem = { instrument: { id: number; marketCode: string; displayName: string }; dataTypes: string[]; status?: string };
type AggregationWorkItem = { instrument: { id: number; marketCode: string; displayName: string }; unit: string; status: string; rowsWritten: number };

export function useSystemManagement() {
  const [snapshot, setSnapshot] = useState<SystemManagementSnapshot | null>(null);
  const [connectionStatus, setConnectionStatus] = useState<"connecting" | "live" | "offline">("offline");
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let disposed = false;
    let socket: WebSocket | undefined;
    const connect = () => {
      if (disposed) return;
      setConnectionStatus("connecting");
      socket = new WebSocket(systemManagementWebSocketUrl());
      socket.onmessage = (event) => {
        const message = JSON.parse(String(event.data)) as { type: string; payload: SystemManagementSnapshot };
        if (message.type !== "system.snapshot") return;
        setSnapshot(message.payload);
        setConnectionStatus("live");
      };
      socket.onclose = () => {
        if (disposed) return;
        setConnectionStatus("offline");
        timer = setTimeout(connect, 1_000);
      };
      socket.onerror = () => socket?.close();
    };
    connect();
    return () => { disposed = true; if (timer) clearTimeout(timer); socket?.close(); };
  }, []);
  return { snapshot, connectionStatus };
}
