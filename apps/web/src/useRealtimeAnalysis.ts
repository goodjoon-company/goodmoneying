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

type SubscriptionGate = {
  socket: WebSocket;
  nextGeneration: number;
  currentGeneration: number;
  acceptedGeneration: number | null;
  pendingGenerations: number[];
};

export function useRealtimeAnalysis(
  instrumentId: number | null,
  unit: AnalysisUnit,
  rangeDays: AnalysisRangeDays
): AnalysisState & { connectionStatus: "connecting" | "live" | "offline" } {
  const [state, setState] = useState<AnalysisState>(initialAnalysisState);
  const [connectionStatus, setConnectionStatus] = useState<"connecting" | "live" | "offline">("offline");
  const socketRef = useRef<WebSocket | null>(null);
  const lastSentSubscriptionRef = useRef<{ socket: WebSocket; key: string } | null>(null);
  const subscriptionGateRef = useRef<SubscriptionGate | null>(null);
  const subscriptionRef = useRef({ instrumentId, unit, rangeDays });
  subscriptionRef.current = { instrumentId, unit, rangeDays };
  const hasSubscription = instrumentId !== null;

  useEffect(() => {
    setState(initialAnalysisState);
    const socket = socketRef.current;
    if (instrumentId !== null && isWebSocketOpen(socket)) {
      const sent = sendSubscriptionIfChanged(
        socket,
        { instrumentId, unit, rangeDays },
        lastSentSubscriptionRef
      );
      if (sent) recordSubscriptionGeneration(socket, subscriptionGateRef);
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
          if (sent) recordSubscriptionGeneration(socket, subscriptionGateRef);
        }
      };
      socket.onmessage = (event) => {
        if (disposed || socketRef.current !== socket) return;
        const message = JSON.parse(String(event.data)) as AnalysisMessage;
        if (message.type === "analysis.session") {
          if (acceptSubscriptionGeneration(socket, subscriptionGateRef)) {
            setConnectionStatus("live");
          }
          return;
        }
        if (message.type === "analysis.error" && hasPendingGeneration(socket, subscriptionGateRef)) {
          if (!acceptSubscriptionGeneration(socket, subscriptionGateRef)) return;
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

  return { ...state, connectionStatus };
}

function sendSubscriptionIfChanged(
  socket: WebSocket,
  subscription: { instrumentId: number; unit: AnalysisUnit; rangeDays: AnalysisRangeDays },
  lastSentSubscriptionRef: {
    current: { socket: WebSocket; key: string } | null;
  }
): boolean {
  const key = `${subscription.instrumentId}:${subscription.unit}:${subscription.rangeDays}`;
  const lastSent = lastSentSubscriptionRef.current;
  if (lastSent?.socket === socket && lastSent.key === key) return false;
  sendSubscription(socket, subscription);
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
  subscriptionGateRef: { current: SubscriptionGate | null }
) {
  const gate = subscriptionGateRef.current;
  if (gate?.socket !== socket) return;
  gate.nextGeneration += 1;
  gate.currentGeneration = gate.nextGeneration;
  gate.acceptedGeneration = null;
  gate.pendingGenerations.push(gate.currentGeneration);
}

function acceptSubscriptionGeneration(
  socket: WebSocket,
  subscriptionGateRef: { current: SubscriptionGate | null }
): boolean {
  const gate = subscriptionGateRef.current;
  if (gate?.socket !== socket) return false;
  const generation = gate.pendingGenerations.shift();
  if (generation === undefined || generation !== gate.currentGeneration) return false;
  gate.acceptedGeneration = generation;
  return true;
}

function hasPendingGeneration(
  socket: WebSocket,
  subscriptionGateRef: { current: SubscriptionGate | null }
): boolean {
  const gate = subscriptionGateRef.current;
  return gate?.socket === socket && gate.pendingGenerations.length > 0;
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
