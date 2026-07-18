# 백테스트 진행 WebSocket 계약(Backtest Progress WebSocket Contract)

상태: P4-9 구현 계약(Implemented Slice)
날짜: 2026-07-18

## Endpoint

- `ws://<api-host>/v1/backtest-runs/{backtestRunId}/progress`

이 WebSocket은 Backtest Worker가 처리하는 run의 현재 진행 snapshot을 보낸다. P4-9 최소 계약은 연결 직후 현재 `BacktestRunSummary`에서 파생한 snapshot 한 건을 전송하고 연결을 닫는다. 후속 live tail 확장 시 같은 메시지 타입을 반복 전송할 수 있으나, 클라이언트는 같은 `backtestRunId`와 더 최신 `status`를 가진 메시지로 상태를 갱신해야 한다.

## 진행 메시지

```json
{
  "version": "1",
  "type": "backtest.progress",
  "backtestRunId": 21,
  "status": "pending",
  "progressPercent": "0",
  "isTerminal": false,
  "inputHash": "eeee...",
  "resultHash": null,
  "requestedAt": "2026-07-18T00:00:00Z",
  "startedAt": null,
  "finishedAt": null
}
```

### Progress mapping

| `status` | `progressPercent` | `isTerminal` |
| --- | ---: | --- |
| `pending` | `0` | `false` |
| `running` | `50` | `false` |
| `succeeded` | `100` | `true` |
| `failed` | `100` | `true` |
| `cancelled` | `100` | `true` |

P4-9는 별도 세부 progress column을 추가하지 않는다. 진행률은 기존 run 상태에서 파생한 UI 표시 값이다. Worker 내부 단계별 progress가 필요하면 별도 이벤트 저장소 또는 progress row 계약을 추가해야 한다.

## 오류 메시지

없는 run은 연결을 수락한 뒤 안정 오류 메시지를 한 번 전송하고 닫는다.

```json
{
  "version": "1",
  "type": "backtest.error",
  "code": "BACKTEST_RUN_NOT_FOUND",
  "message": "백테스트 실행 결과가 없습니다.",
  "backtestRunId": 999
}
```
