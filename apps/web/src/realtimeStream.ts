export type RealtimeStreamMessageType =
  | "subscribed"
  | "event"
  | "heartbeat"
  | "snapshot_required"
  | "slow_consumer"
  | "error";

export type RealtimeStreamEnvelope<TPayload = unknown> = {
  schema_version: string;
  topic: string;
  scope: string;
  event_id: string;
  sequence: number;
  cursor: string;
  occurred_at: string;
  published_at: string;
  message_type: RealtimeStreamMessageType;
  payload: TPayload;
};

export type RealtimeStreamTracker = {
  lastSequenceByStream: Map<string, number>;
  pausedStreams: Set<string>;
};

export type RealtimeStreamConsumeResult<TPayload> =
  | { kind: "payload"; payload: TPayload; cursor: string }
  | { kind: "heartbeat"; cursor: string }
  | { kind: "snapshot_required"; message: string; cursor: string }
  | { kind: "ignored"; cursor: string | null };

export function createRealtimeStreamTracker(): RealtimeStreamTracker {
  return {
    lastSequenceByStream: new Map(),
    pausedStreams: new Set()
  };
}

export function isRealtimeStreamEnvelope(value: unknown): value is RealtimeStreamEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    candidate.schema_version === "1.0" &&
    typeof candidate.topic === "string" &&
    typeof candidate.scope === "string" &&
    typeof candidate.sequence === "number" &&
    typeof candidate.cursor === "string" &&
    typeof candidate.message_type === "string" &&
    "payload" in candidate
  );
}

export function consumeRealtimeEnvelope<TPayload>(
  tracker: RealtimeStreamTracker,
  envelope: RealtimeStreamEnvelope<TPayload>
): RealtimeStreamConsumeResult<TPayload> {
  const streamKey = `${envelope.topic}\u0000${envelope.scope}`;
  const lastSequence = tracker.lastSequenceByStream.get(streamKey);

  if (envelope.message_type === "subscribed") {
    tracker.pausedStreams.delete(streamKey);
    tracker.lastSequenceByStream.set(streamKey, envelope.sequence);
    return { kind: "payload", payload: envelope.payload, cursor: envelope.cursor };
  }

  if (tracker.pausedStreams.has(streamKey)) {
    return {
      kind: "snapshot_required",
      message: "실시간 스트림 누락 이후 REST snapshot 복구가 필요합니다.",
      cursor: envelope.cursor
    };
  }

  if (envelope.message_type === "heartbeat") {
    if (lastSequence === undefined || envelope.sequence >= lastSequence) {
      tracker.lastSequenceByStream.set(streamKey, envelope.sequence);
    }
    return { kind: "heartbeat", cursor: envelope.cursor };
  }

  if (envelope.message_type === "snapshot_required" || envelope.message_type === "slow_consumer") {
    tracker.pausedStreams.add(streamKey);
    return {
      kind: "snapshot_required",
      message: snapshotRequiredMessage(envelope.payload),
      cursor: envelope.cursor
    };
  }

  if (envelope.message_type === "error") {
    return { kind: "payload", payload: envelope.payload, cursor: envelope.cursor };
  }

  if (lastSequence !== undefined && envelope.sequence <= lastSequence) {
    return { kind: "ignored", cursor: envelope.cursor };
  }

  if (lastSequence !== undefined && envelope.sequence !== lastSequence + 1) {
    tracker.pausedStreams.add(streamKey);
    return {
      kind: "snapshot_required",
      message: `실시간 스트림 sequence gap(${lastSequence} → ${envelope.sequence})이 감지되어 REST snapshot 복구가 필요합니다.`,
      cursor: envelope.cursor
    };
  }

  tracker.lastSequenceByStream.set(streamKey, envelope.sequence);
  return { kind: "payload", payload: envelope.payload, cursor: envelope.cursor };
}

function snapshotRequiredMessage(payload: unknown): string {
  if (typeof payload !== "object" || payload === null) {
    return "실시간 스트림 복구가 필요합니다.";
  }
  const message = (payload as Record<string, unknown>).message;
  return typeof message === "string" ? message : "실시간 스트림 복구가 필요합니다.";
}
