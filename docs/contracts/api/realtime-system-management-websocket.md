# 시스템 관리 실시간 웹소켓(WebSocket) 계약

- 경로: `GET /v1/realtime/system-management` WebSocket 업그레이드(upgrade)
- 서버는 구독 메시지 없이 1초마다 `system.snapshot` 하나만 전송한다.
- 한 메시지는 화면 전체를 갱신할 수 있는 운영 상태만 담으며, 코인별 목록은 최대 표시 수로 잘라 클라이언트가 렌더링한다.

## `system.snapshot`

```json
{
  "version": "1",
  "type": "system.snapshot",
  "sentAt": "2026-07-14T09:00:00+09:00",
  "payload": {
    "refreshedAt": "2026-07-14T09:00:00+09:00",
    "realtime": { "status": "running", "statusLabel": "정상", "items": [] },
    "backfill": { "status": "running", "statusLabel": "정상", "items": [] },
    "aggregationWorker": {
      "status": "running", "statusLabel": "동작 중",
      "statusDetail": "최근 heartbeat 정상",
      "lastHeartbeatAt": "2026-07-14T08:59:55+09:00"
    },
    "aggregation": {
      "id": 12, "status": "running", "progressPercent": "42",
      "totalTargetCount": 140, "completedTargetCount": 59,
      "runningTargetCount": 1, "pendingTargetCount": 80,
      "failedTargetCount": 0, "items": []
    }
  }
}
```

`realtime.items`와 `backfill.items`는 `instrument`, `dataTypes`를 포함한다.

`aggregationWorker`는 집계 작업 상태와 독립적인 실제 `candle_aggregation` 워커 하트비트(heartbeat) 상태다. `status`는 `running`, `stale`, `failed` 중 하나이며, `lastHeartbeatAt`은 하트비트 기록이 없으면 `null`이다. 워커는 실행 중 처리 행 수와 독립된 5초 주기 하트비트를 기록한다. 따라서 `stale`은 장기 대상을 정상 처리 중이라는 뜻이 아니라 워커 하트비트가 30초 넘게 없다는 뜻이다. 내부 실행·저장 경계 결정은 [ADR-0009](../../ADR/ADR-0009-캔들-집계-테이블과-자동-워커.md)를 따른다.

`aggregation`의 대상 수는 `totalTargetCount = completedTargetCount + runningTargetCount + pendingTargetCount + failedTargetCount`를 만족한다. `aggregation.items`는 `instrument`, `unit`, `status`, `rowsWritten`을 포함한다. 집계 작업이 없으면 `aggregation`은 `null`이지만 `aggregationWorker`는 항상 전송한다.
