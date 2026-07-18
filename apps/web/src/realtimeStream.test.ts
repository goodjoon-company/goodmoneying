import { describe, expect, test } from "vitest";
import {
  createRealtimeStreamTracker,
  consumeRealtimeEnvelope,
  type RealtimeStreamEnvelope
} from "./realtimeStream";

function envelope(
  sequence: number,
  payload: { type: string },
  messageType: RealtimeStreamEnvelope["message_type"] = "event"
): RealtimeStreamEnvelope<{ type: string }> {
  return {
    schema_version: "1.0",
    topic: "analysis.instrument:1:1m:365",
    scope: "operator:local",
    event_id: `evt-${sequence}`,
    sequence,
    cursor: `cursor-${sequence}`,
    occurred_at: "2026-07-18T06:30:00Z",
    published_at: "2026-07-18T06:30:00Z",
    message_type: messageType,
    payload
  };
}

describe("P2 실시간 스트림 envelope 추적", () => {
  test("순차 event는 payload로 unwrap하고 cursor를 갱신한다", () => {
    const tracker = createRealtimeStreamTracker();

    const first = consumeRealtimeEnvelope(tracker, envelope(1, { type: "analysis.instrument" }));
    const second = consumeRealtimeEnvelope(tracker, envelope(2, { type: "analysis.market" }));

    expect(first.kind).toBe("payload");
    expect(second.kind).toBe("payload");
    expect(second.cursor).toBe("cursor-2");
  });

  test("중복과 역순 event는 reducer에 전달하지 않는다", () => {
    const tracker = createRealtimeStreamTracker();
    consumeRealtimeEnvelope(tracker, envelope(1, { type: "analysis.instrument" }));
    consumeRealtimeEnvelope(tracker, envelope(2, { type: "analysis.market" }));

    expect(consumeRealtimeEnvelope(tracker, envelope(2, { type: "analysis.market" })).kind).toBe("ignored");
    expect(consumeRealtimeEnvelope(tracker, envelope(1, { type: "analysis.instrument" })).kind).toBe("ignored");
  });

  test("sequence gap은 snapshot_required로 전환하고 이후 event 적용을 멈춘다", () => {
    const tracker = createRealtimeStreamTracker();
    consumeRealtimeEnvelope(tracker, envelope(1, { type: "analysis.instrument" }));

    const gap = consumeRealtimeEnvelope(tracker, envelope(3, { type: "analysis.market" }));
    const afterGap = consumeRealtimeEnvelope(tracker, envelope(4, { type: "analysis.chart" }));

    expect(gap.kind).toBe("snapshot_required");
    expect(afterGap.kind).toBe("snapshot_required");
  });

  test("heartbeat는 lastSequence만 검증하고 payload를 reducer에 전달하지 않는다", () => {
    const tracker = createRealtimeStreamTracker();
    consumeRealtimeEnvelope(tracker, envelope(1, { type: "analysis.instrument" }));

    const heartbeat = consumeRealtimeEnvelope(
      tracker,
      envelope(1, { type: "stream.heartbeat" }, "heartbeat")
    );

    expect(heartbeat.kind).toBe("heartbeat");
  });
});
