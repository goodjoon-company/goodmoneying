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
    "aggregation": {
      "id": 12, "status": "running", "progressPercent": "42",
      "totalTargetCount": 140, "completedTargetCount": 59,
      "runningTargetCount": 1, "failedTargetCount": 0, "items": []
    }
  }
}
```

`realtime.items`와 `backfill.items`는 `instrument`, `dataTypes`를 포함한다. `aggregation.items`는 `instrument`, `unit`, `status`, `rowsWritten`을 포함한다. 집계가 최신이면 `aggregation`은 `null`이다.
