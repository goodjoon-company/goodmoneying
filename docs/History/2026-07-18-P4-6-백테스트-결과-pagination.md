# 2026-07-18 P4-6 백테스트 결과 pagination

검증: [P4-6 백테스트 결과 pagination 검증](../Test/2026-07-18-P4-6-백테스트-결과-pagination-검증.md)

## 변경 요약

- `GET /v1/backtest-runs/{backtestRunId}/trades`를 추가해 체결 결과를 `trade_sequence` 오름차순으로 페이지 조회한다.
- `GET /v1/backtest-runs/{backtestRunId}/equity-points`를 추가해 자산곡선을 `point_sequence` 오름차순으로 페이지 조회한다.
- cursor는 run ID, 첫 페이지 sequence 상한, 마지막 sequence와 HMAC digest를 포함한다.
- 단건 `BacktestRun` 상세 응답은 기존 호환을 유지한다.
- Web API client에 대용량 결과 페이지 함수 2개를 추가했다.

## 후속 작업

- Backtest 실행 생성 API와 replay materializer
- WebSocket 진행 이벤트
- walk-forward, sensitivity, bootstrap artifact
