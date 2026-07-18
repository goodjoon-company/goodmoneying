# 백테스트 엔진 계약(Backtest Engine Contract)

상태: P4-1 구현 계약(Implemented Slice)
버전: backtest-core-v1
날짜: 2026-07-18

이 문서는 P4 백테스트 엔진의 순수 도메인 경계(pure domain boundary)를 정의한다. P4-1은 `packages/shared/goodmoneying_shared/backtest_engine.py`의 결정론적 캔들 재생 엔진, P4-2는 `backtest_runs` 계열 영속화, P4-3은 저장된 run을 읽는 REST와 Backtest Lab 조회 화면, P4-4는 저장된 run 목록과 안정 cursor 조회를 구현했다. Worker 실행 접수, 대용량 결과 pagination, WebSocket 진행 이벤트는 후속 조각이다.

## 1. 입력 고정값

run 입력은 다음 값을 모두 포함해 `input_hash`를 만든다.

- `dataset_version_id`
- dataset content hash
- `strategy_version_id`
- strategy graph hash
- `engine_version`: `backtest-core-v1`
- parameter hash
- deterministic seed
- `initial_cash`
- execution model
- 정렬된 사건 목록
- golden replay 신호 목록
- 체결 가정 목록

`input_hash`와 `result_hash`는 정규 JSON SHA-256으로 계산한다. 정규화는 키 정렬, UTC RFC 3339 시각, Decimal 문자열을 사용한다. DB 대리키, Python 객체 주소, dictionary 삽입 순서, wall clock, 직접 난수 호출은 hash 입력에 넣지 않는다.

## 2. 사건 재생 순서

캔들 사건은 `(knowledge_at, source_priority, stable_sequence)`로 정렬한다. `knowledge_at`은 백테스트 엔진이 해당 사실을 알 수 있게 된 시각이다. `occurred_at`보다 빠른 `knowledge_at`을 가진 신호는 미래 데이터 접근(look-ahead)으로 보고 run을 실패시킨다.

## 3. 체결 모델

P4-1 엔진은 호가가 없는 캔들 재생을 지원하며, 모든 체결 결과에 다음 가정을 기록한다.

- `orderbook_absent_uses_candle_close`: orderbook이 없으면 캔들 close를 기준 가격으로 사용한다.
- `partial_fill_by_candle_volume_participation`: 체결 가능 수량은 캔들 volume과 참여율로 제한하고 잔량을 기록한다.

execution model 필드는 다음과 같다.

- `fee_rate`: 체결 금액에 곱하는 수수료율
- `slippage_bps`: 매수는 close 위, 매도는 close 아래로 적용하는 슬리피지(slippage) basis point
- `latency_seconds`: 신호 지식 시각 뒤 체결 가능한 첫 사건까지의 지연
- `max_participation_rate`: 캔들 volume 중 엔진이 사용할 수 있는 최대 비율

신호의 `knowledge_at + latency_seconds` 이후에 체결 가능한 사건이 없으면 엔진은 현재 또는 과거 사건으로 되돌아가 체결하지 않는다. 이 경우 run은 성공 상태를 유지하되 해당 신호의 거래를 생성하지 않는다.

가격·수량·수수료·현금·성과는 모두 Decimal로 계산한다. float 변환이나 JSON number 반올림을 엔진 의미에 포함하지 않는다.

Decimal이 아닌 런타임 숫자 입력이 들어오면 엔진은 산술 예외로 중단하지 않고 `decimal_required` 오류를 가진 실패 결과로 수렴한다.

## 4. 결과

결과는 다음 값을 포함한다.

- `status`: `succeeded | failed`
- `input_hash`
- `result_hash`
- `assumptions`
- `replay_events`
- `trades`
- `equity_points`
- `metrics.finalEquity`
- golden replay 신호
- `errors`

동일 dataset content hash, strategy graph hash, engine version, parameter hash, deterministic seed, execution model, 사건 내용, 신호 내용은 동일한 `input_hash`, `result_hash`, 성과를 반환해야 한다.

## 5. 후속 확장

P4 후속 조각은 이 순수 엔진을 유지한 채 다음 계약을 추가한다.

- `backtest_runs`, `backtest_trades`, `backtest_equity_points`, `backtest_metrics`, `backtest_artifacts` DB migration
- `GET /v1/backtest-runs`, `GET /v1/backtest-runs/{backtestRunId}` 조회 API와 Backtest Lab 읽기 전용 화면
- run 목록은 `BacktestRunSummary`만 반환하고 상세 체결·산출물은 단건 조회에 둔다.
- 목록 cursor는 `backtest-run-list-v1` 문맥, 첫 페이지 최대 ID 상한(`ceiling`), 마지막 ID(`lastId`), HMAC-SHA-256 digest를 담은 불투명 값이다. 다음 페이지는 `id <= ceiling AND id < lastId ORDER BY id DESC LIMIT pageSize + 1`로 조회해 신규 삽입이 기존 cursor 페이지에 섞이지 않는다. 운영에서 재시작 후 cursor 연속성이 필요하면 `GOODMONEYING_CURSOR_HMAC_SECRET`을 설정한다. 없으면 프로세스 시작 시 생성한 비밀로 서명해 재시작 전 cursor만 유효하다.
- Backtest Worker 임대·재시도·산출물 저장
- 대용량 결과 pagination, WebSocket 진행 이벤트
- walk-forward, sensitivity, bootstrap metric artifact
