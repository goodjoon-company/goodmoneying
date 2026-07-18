# 시스템 트레이딩 도메인 설계(Module Design)

상태: 승인된 목표 설계(Accepted Target), P1·P2·P3-1 기계 계약 구현 중
버전: 1.5.0
날짜: 2026-07-18

## 1. 목적

이 문서는 DB migration, OpenAPI, 내부 WebSocket schema와 도메인 코드가 공통으로 구현해야 할 식별자, 상태, 불변 조건의 목표 설계다. 이 Markdown은 기계 계약의 단일 기준이 아니다. P1은 `docs/contracts/db/migrations/20260717000100_system_trading_data_foundation.sql`과 OpenAPI로 구현됐다. Issue #29~#33은 해당 수직 조각을 구현할 때 계약을 먼저 추가하고 자동 검증해야 하며, 그 전에는 운영 구현 완료로 간주하지 않는다.

## 2. 공통 표현

- 식별자는 DB bigint 또는 UUID를 내부에서 사용하고 API에서는 문자열로 직렬화할 수 있다.
- 모든 시각 wire format은 UTC RFC 3339이며 `Z` 또는 명시 offset을 요구한다.
- 가격·수량·금액·수수료·손익은 JSON number가 아니라 정규화된 decimal 문자열이다.
- 모든 변경 명령은 `request_id`, `idempotency_key`, `actor_id`, `reason`, `requested_at`을 가진다.
- 불변 버전은 생성 후 수정·삭제하지 않고 새 버전을 만든다.
- `owner_id`는 공개 사용자 계정이 아니라 단일 운영자 principal의 안정된 식별자다. 초기 값은 bootstrap 과정에서 생성하고 secret이나 email을 식별자로 사용하지 않는다.

## 3. 핵심 엔터티와 자연키

| 영역 | 엔터티 | 자연키 또는 불변 조건 |
|---|---|---|
| 시장 | `markets` | `(exchange, market_code)` |
| 시장 | `market_status_history` | `(market_id, valid_from)`, 기간 중첩 금지 |
| 수집 | `collection_policies` | `(exchange, quote_currency, name)` |
| 수집 | `collection_target_specs` | `(policy_id, market_id, data_type, candle_unit)` |
| 수집 | `collection_runs` | `(worker_role, run_key)` |
| 수집 | `backfill_jobs` | `idempotency_key` 고유, 임대 소유자·만료 시각 동시 갱신 |
| 품질 | `coverage_intervals` | `(target_spec_id, range_start_at, range_end_at, status)`, 동일 대상 기간 중첩 금지 |
| 품질 | `data_quality_events` | `(target_spec_id, event_type, detected_at, fingerprint)` |
| 품질 | `fetch_manifests` | `(source, request_fingerprint, requested_at)` |
| 원천 | `source_candles` | `(market_id, candle_unit, candle_start_at)` |
| 원천 이력 | `source_candle_revisions` | `(source_candle_id, revision_number)` 및 내용 해시 멱등 키 |
| 원천 | `trade_events` | `(market_id, exchange_trade_id)` |
| 원천 | `orderbook_snapshots` | `(instrument_id, source, occurred_at, payload_checksum)`; 동일 시각의 내용 변경은 별도 사실로 보존 |
| 원천 | `source_receipts` | `(connection_id, frame_sequence)`; 전체 원본 JSON과 수신 provenance를 append-only 보존 |
| 원천 | `ticker_snapshots` | `(market_id, occurred_at)` |
| 파생 이력 | `candle_rollups` | 대리 ID, `(instrument_id, candle_unit, candle_start_at, calculation_version, input_content_hash, coverage_snapshot_hash, source_revision_through_id, quality_event_through_id)` 고유, 추가 전용 |
| 파생 전파 | `candle_rollup_invalidations` | 범위·원천 개정 상한·품질 이벤트 상한·커버리지 스냅샷 해시를 가진 멱등 키 |
| 파생 작업 | `candle_rollup_recompute_jobs` | 무효화당 하나, 임대·fencing·재시도·dead-letter·safe-restart |
| 파생 통계 | `market_statistics` | 시장·주기·발생 시각·계산 버전·현재 입력·프런티어·내용 해시, 추가 전용 부모 계보 |
| 지표 | `indicator_definitions` | `indicator_key` 고유 |
| 지표 | `indicator_definition_versions` | `(definition_id, version)`, 알고리즘·파라미터·정밀도·반올림·정의 해시 불변 |
| 지표 | `indicator_materializations` | 상품·주기·발생 시각·정의 집합 해시·현재 입력·프런티어·내용 해시, 추가 전용 부모 계보 |
| 지표 | `indicator_values` | `(materialization_id, definition_version_id, value_name)`, 부모 값 계보 |
| 지표 작업 | `indicator_invalidations` | 원천 개정·집계 개정·품질 이벤트·집계 무효화 중 정확히 하나의 원인, DB 시계 임대·generation fencing |
| 미시구조 정의 | `microstructure_definition_versions` | 계산 버전·공식·Decimal 정밀도·반올림·정의 해시 불변 |
| 미시구조 결과 | `microstructure_materializations` | 상품·1분 구간·원천 프런티어·지식 시각·입력 계보 해시, 추가 전용 부모 계보 |
| 미시구조 값 | `microstructure_statistics` | 물질화당 하나, 계산 상태와 5단계 호가·체결 품질 분리, 원천 캔들 대사 계보 |
| 미시구조 작업 | `microstructure_invalidations` | 상품별 닫힌 1분 범위 병합, DB 임대·세대 fencing·재시도·dead-letter |
| 데이터셋 빌드 | `dataset_builds`, `dataset_build_series` | 요청 트랜잭션에서 선택 hash·asOf·원천/품질/물질화/시장 상태 ceiling 고정, 멱등 키 충돌 차단, 임대 heartbeat·세대 fencing·제한 재시도·dead-letter |
| 연구 | `dataset_versions`, `dataset_version_series` | 대리키를 제외한 canonical content hash 불변·고유, 입력/출력 반개방 범위와 정책 고정, 게시 전 child 완성 뒤 원자적 봉인 |
| 연구 UI | Data Lab | `/v1/data-foundation`의 `instrumentId`로 KRW 시장을 선택하고 `/v1/dataset-builds`, `/v1/dataset-versions`, coverage, series REST 계약만 읽어 build 생성·복제·비교를 수행 |
| 내부 스트림 | `internal-realtime-stream.schema.json`, `realtime_stream.py`, `realtimeStream.ts` | topic·scope별 sequence, 서명 cursor, heartbeat, snapshot_required, 클라이언트 gap 적용 중단 |
| 데이터셋 입력 | `dataset_version_candles`, `dataset_version_indicators`, `dataset_version_market_statistics`, `dataset_version_microstructures` | 정확한 불변 입력 FK, 자연키·내용 해시·지식 시각 보존 |
| 데이터셋 시점 상태 | `dataset_version_market_status_snapshots` | 생성 프런티어의 시장 상태·거래 가능 구간과 coverage 의미 복제 |
| 전략 | `strategy_definitions` | `(owner_id, name)`, 정의 생성 명령의 멱등 키 고유 |
| 전략 | `strategy_versions` | `(strategy_id, version)`, graph hash·검증 결과·명령 증적 불변 |
| 전략 | `strategy_graphs` | `strategy_version_id`당 하나, `schema_version='strategy-graph-v1'`, 순환 금지 |
| 전략 | `strategy_parameters` | `(strategy_version_id, name)`, version publish 뒤 의미 변경 금지 |
| 백테스트 | `backtest_runs` | `(strategy_version_id, dataset_version_id, engine_version, parameter_hash, seed)` |
| 백테스트 | `backtest_trades` | `(run_id, trade_sequence)` |
| 백테스트 | `backtest_equity_points` | `(run_id, occurred_at)` |
| 백테스트 | `backtest_metrics` | `(run_id, metric_name, scope_key)` |
| 백테스트 | `backtest_artifacts` | `(run_id, artifact_type, content_hash)` |
| 계좌 | `exchange_accounts` | `(exchange, account_alias)`; secret 저장 금지 |
| 포트폴리오 | `portfolios` | `(owner_id, name)`, 현재 자산·현금 projection 경계 |
| 포트폴리오 | `portfolio_policies` | `(owner_id, name, version)` |
| 포트폴리오 | `capital_allocations` | `(portfolio_policy_id, scope_type, scope_id)` |
| 봇 | `bot_definitions` | `(owner_id, name)` |
| 봇 | `bot_versions` | `(bot_id, version)`, 설정 불변 |
| 봇 | `bot_instances` | `(bot_version_id, instance_key)` |
| 봇 | `bot_state_transitions` | `(bot_instance_id, transition_sequence)` |
| 주문 | `order_intents` | `idempotency_key` 전역 고유 |
| 주문 | `exchange_orders` | `(exchange_account_id, exchange_order_id)` 또는 `(exchange_account_id, identifier)`; identifier는 제출 전 영속 |
| 주문 | `fills` | `(exchange_account_id, exchange_trade_id)` |
| 포지션 | `position_events` | `(portfolio_id, event_sequence)`, fill·adjustment append-only ledger |
| 포지션 | `positions` | `(portfolio_id, market_id)` 현재 projection, `position_events`로 재구성 가능 |
| 위험 | `risk_limits` | `(scope_type, scope_id, limit_type, version)` |
| 위험 | `risk_events` | `(scope_type, scope_id, occurred_at, fingerprint)` |
| 대사 | `reconciliation_runs` | `(exchange_account_id, run_key)` |
| 감사 | `audit_events` | append-only, `(occurred_at, sequence)` |

Upbit 호가 payload에는 거래소 전역 sequence가 없다. 공식 payload의 millisecond `timestamp`를 사건 시각으로, 정규화 전 전체 JSON의 결정적 직렬화 SHA-256을 `payload_checksum`으로 사용한다. 전역 고유 `connection_id`와 연결 내부 `frame_sequence`는 같은 연결 안의 전달 provenance와 재처리 멱등 키이며, 재연결 시 frame sequence가 초기화되므로 연결 간 사건 순서나 snapshot 자연키로 사용하지 않는다. `source_receipts.id`는 PostgreSQL이 영속화 시 부여하는 단조 증가 identity이며 거래소 sequence가 아니라 같은 수신 시각의 안정적 tie-breaker다. 같은 instrument·source·사건 시각·checksum의 snapshot은 한 경제적 상태로 흡수하되 A-B-A 재등장과 재연결 중복을 포함한 모든 수신은 별도 `source_receipts` 행으로 보존한다. 같은 timestamp에 내용이 다르면 두 snapshot을 모두 보존한다. 감사 재생은 receipt의 원본 JSON을 `(received_at, source_receipts.id)`로 정렬하고 snapshot·summary는 instrument·사건 시각·checksum으로 연결한다.

P2-4부터 새 체결과 호가 정규화 행은 해당 `source_receipts.id`를 직접 참조하며 receipt·정규화 행·미시구조 더티 범위를 하나의 트랜잭션에서 확정한다. 체결의 `BID`는 공식 문서의 매수, `ASK`는 매도 표시만 뜻하며 공격자(aggressor)·메이커/테이커를 추론하지 않는다. 미시구조 v1은 UTC 1분 반개방 구간만 지원한다. 호가는 `level=0`인 마지막 유효 스냅숏의 상위 10단계, 체결은 같은 구간 원천을 사용하고 원천 캔들 개정의 거래량·거래대금과 대사한다. 계산 불능과 원천 품질은 별도 필드이며 임의 0, 무한대, 상한값 또는 전방 채움을 만들지 않는다. 정확한 공식과 wire 필드는 ADR-0020과 기계 계약을 따른다.

### 3.1 기존 DB 무손실 전환

Issue #28 migration은 기존 이름을 즉시 삭제·변경하지 않고 다음 확장-수축 순서를 따른다.

| 기존 | 목표 | 전환 규칙 |
|---|---|---|
| `instruments` | `markets` | 새 `markets.legacy_instrument_id` unique FK로 1:1 backfill하고 API read를 dual-read 후 목표로 전환 |
| `collection_plans` | `collection_policies` | 기존 preset·range·continuous·status를 이름이 `legacy-default`인 policy와 target 설정으로 보존 |
| 종목당 `collection_targets` 1행 | policy·market·data_type·unit target | 기존 target마다 실제 수집 data type·unit을 fan-out하고 `legacy_target_id`와 row count checksum 보존 |
| `orderbook_summaries` | `orderbook_snapshots` | summary는 역사 자료로 보존하고 원천 snapshot으로 승격하지 않음; 새 snapshot 수집 시점부터 별도 저장 |
| `audit_logs` | `audit_events` | 기존 payload·occurred_at을 append-only legacy event로 복사하고 원본 유지 |

전환은 새 table·nullable FK 추가 → 기존 row backfill → old/new row count·key·decimal·absolute timestamp checksum → dual-write → consumer 전환 → 최소 한 release 호환 관찰 → 별도 승인 후 old path 수축 순서다. 운영 backup·restore rehearsal 전에는 수축·삭제를 수행하지 않는다.

## 4. 상태 열거형

### 4.1 커버리지

`available | no_trade | missing | unavailable | unverified`

판정 우선순위와 증거는 다음과 같다.

1. 공식 시장 상태상 아직 상장 전·거래 종료 후이거나 API의 동적 보존 범위 밖이면 `unavailable`이다.
2. 업비트 분 캔들 성공 응답에서 양쪽 인접 캔들이 완전히 경계한 내부 분 공백만 `no_trade`다. 빈 응답과 페이지 선두·후미 공백은 `unverified` 또는 기존 상태를 유지한다.
3. 자연키 원천 행과 manifest checksum이 있으면 `available`이다.
4. 시도했으나 4xx·5xx·timeout·decode·persistence 오류가 retry budget을 소진하지 않았으면 interval은 `unverified`, 소진하면 복구 대상인 `missing`이다.
5. 아직 시도·판정하지 않은 구간은 `unverified`다.

후속 공식 evidence가 도착하면 `unverified|missing → available|no_trade|unavailable`로 정정할 수 있고 이전 판단은 fetch manifest와 연결된 `data_quality_events` 이력으로 보존한다. 모든 상태 생성·전이는 이전·새 상태, 반개방 범위, 사유 코드, 평가 시각을 남기며 같은 상태와 같은 증거의 재적용은 중복 이벤트를 만들지 않는다.

공식 시장 카탈로그에서 거래 중단 또는 카탈로그 누락을 관측하면 관측 시각부터 도메인상 열린 끝까지 `unavailable`이다. PostgreSQL은 무한 시각을 애플리케이션에서 안전하게 읽고 분할하기 위해 열린 끝을 `9999-01-01T00:00:00Z` sentinel로 저장한다. 이 값은 실제 사건 시각이나 거래 종료 예상 시각이 아니며 도메인·API에서는 상한이 정해지지 않은 구간으로 해석한다.

같은 시장이 다시 거래 가능 상태로 나타나면 기존 시장 사유의 `unavailable` 구간을 재등장 관측 시각에서 닫고, 그 시각 이후는 원천 행이나 성공 응답 증거가 아직 없으므로 `unverified`로 전이한다. 이 결정은 `CoverageEvidence.market_trading_resumed`를 받는 공통 `classify_coverage()`가 내리고, 이후 실제 원천·무거래·획득 불가 증거가 공통 구간 전이 경로를 통해 교체한다. 두 PostgreSQL 저장소는 구간 잠금·반개방 분할·이벤트·fingerprint 생성을 같은 구현으로 사용한다.

### 4.2 내구성 작업

현재 내구성 상태는 `pending | running | retry_wait | succeeded | dead_letter | cancelled`다. `planned | leased | paused | stopped | failed`는 승인 전 계획·수동 운영 제어·전환기 호환 API를 위해 계약에 남아 있으며, 자동 실행 경로는 `leased`를 중간 저장하지 않고 임대 획득과 함께 `running`으로 원자 전이한다.

모든 작업은 `idempotency_key`, `priority`, `attempt_count`, `max_attempts`, `next_retry_at`, `lease_owner`, `lease_expires_at`, `last_error_code`, `dead_letter_reason`, `created_at`, `updated_at`을 가진다. 한 transaction이 `FOR UPDATE SKIP LOCKED`로 eligible row를 선택해 owner·expiry와 상태를 함께 갱신한다. `lease_expires_at`이 지난 `running` 작업만 다른 worker가 회수할 수 있고, 살아 있는 임대는 같은 worker instance도 다시 claim하지 않는다. 결과·진행률 쓰기는 현재 owner와 만료 전 lease를 검증해 회수 전 worker의 늦은 쓰기를 거부한다.

실패 시 시도 예산이 남으면 실패한 target만 `pending`으로 되돌리고 job은 `retry_wait`로 전이해 lease를 해제한다. 429는 다음 초 경계 이후, 418은 응답의 차단 기간 이후에만 다시 eligible하며 응답 기간이 없으면 보수적 5분을 적용한다. 예산을 소진하면 job과 미완료 target을 `dead_letter|failed`로 격리하고 해당 `unverified` coverage를 `missing`으로 바꾸며 `data_quality_events`에 전이를 기록한다. 성공 target과 이미 저장한 원천 행·진행 시각은 재시도에서 보존한다. 수동 pause·stop도 lease를 해제하고 resume은 owner 없는 `pending`으로 돌아간다. `safe-restart`만 시도 예산과 terminal metadata를 명시적으로 초기화한다.

### 4.3 전략·백테스트

- 전략 버전: `draft | validated | published | retired`
- 백테스트: `queued | running | succeeded | failed | cancelled`
- published 전략 버전과 succeeded 백테스트 결과는 변경하지 않는다.

### 4.4 봇

`draft | backtest | paper | shadow | live-ready | live | paused | stopped | faulted`

허용 전이:

- `draft → backtest`
- `backtest → paper`
- `paper → shadow`
- `shadow → live-ready`
- `live-ready → live`는 운영 승인 gate가 있을 때만 허용
- 실행 단계에서 `paused`, `stopped`, `faulted`로 이동 가능
- `stopped`에서 이전 실행 단계로 직접 복귀 금지; 새 instance가 필요
- `paused → paper|shadow|live-ready|live` 복귀는 pause 전 단계와 risk·live gate를 다시 검증한다.
- `faulted → paused`는 reconciliation 성공과 운영자 acknowledgement가 필요하다.
- 각 승격은 승인·거부 사유와 actor를 `bot_state_transitions`에 기록한다.

### 4.5 주문 의도와 거래소 주문

- 주문 의도: `created | risk_rejected | approved | submitted | partially_filled | outcome_unknown | reconciled | cancel_requested | cancelled | completed`
- 거래소 주문: `pending_submit | wait | watch | trade | partially_filled | cancel_requested | done | cancel | prevented | rejected | outcome_unknown`

`outcome_unknown`은 동일 주문 재제출을 금지하고 REST·private WebSocket 대사만 허용한다. 대사는 거래소 주문을 찾으면 실제 상태로, 충분한 조회 창에서 없음을 확인하면 `reconciled`와 명시 결과로 전이한다. 취소 요청 뒤 추가 fill을 허용하고 잔량과 position을 fill 순서대로 갱신한다. `prevented`는 SMP 결과이며 실패로 단순 변환하지 않는다.

주문 의도는 위험 검사 전에 생성한다. 생성과 멱등 키 선점은 하나의 트랜잭션이며 Risk Worker가 사용한 정책 버전·입력 증적·판정 사유를 기록한 뒤 `risk_rejected|approved`로 전이한다. `risk_rejected`는 삭제하거나 같은 키로 재평가하지 않고 변경된 입력은 새 의도로 생성한다.

실거래 제출 전 같은 트랜잭션에서 내부 `idempotency_key`를 Upbit `identifier`에 결정적으로 매핑해 주문 의도·거래소 주문·outbox에 영속한다. 형식은 `gm1_` + `base32lower(sha256(exchange_account_stable_id + ":" + idempotency_key))`이며 padding을 제거한 56자라 공식 최대 64자를 넘지 않는다. `(exchange_account_id, identifier)`는 영구 unique이고 이미 사용된 identifier를 다른 의도에 재사용하지 않는다. timeout 뒤에는 이 identifier와 거래소 UUID로 REST 조회·private WebSocket 대사만 수행하며 동일 주문을 다시 제출하지 않는다. 주문 테스트 API가 반환한 식별자는 조회·취소에 쓸 수 없으므로 live-ready 증적과 실거래 대사 식별자를 혼동하지 않는다.

### 4.6 긴급 정지와 live capability

`trading_capabilities`는 global `live_disabled|live_enabled` 권위 상태와 승인 actor·reason·approved_at·expires_at·deployment_sha를 가진다. 조회 실패·불일치·만료·새 SHA는 `live_disabled`로 평가한다. `kill_switches`는 `global|bot|account` scope, `armed|released`, reason, actor와 sequence를 가진다. 주문 의도 승인 transaction은 capability와 모든 적용 switch를 잠그고 검사해 신규 주문과 race를 차단한다. switch arm 후 진행 주문은 정책에 따라 `leave_open|cancel_open`을 선택하고 결과를 감사한다.

## 5. 전략 그래프 계약

그래프는 `schema_version`, `nodes`, `edges`, `outputs`를 가진다. 노드는 `id`, `type`, `config`, `input_ports`, `output_ports`를 가진다. edge는 `(from_node, from_port, to_node, to_port)`이며 자료형과 시간 주기가 호환돼야 한다.

P3-1은 서버 검증기와 DB/API 계약을 먼저 구현한다. canonical graph hash는 node·edge·output 배열 순서에 의존하지 않고 정규 JSON의 SHA-256으로 계산한다. 전략 버전 게시 명령은 모든 변경 명령 공통 규칙에 따라 `request_id`, `idempotency_key`, `actor_id`, `requested_at`, `reason`을 요구하며 게시된 version의 graph와 검증 결과는 append-only로 보존한다.

검증 오류는 안정된 code를 사용한다.

- `cycle_detected`
- `port_type_mismatch`
- `timeframe_incompatible`
- `look_ahead_detected`
- `parameter_out_of_range`
- `missing_data_policy_required`
- `insufficient_warmup`
- `missing_output`

평가 결과는 `node_id`, `occurred_at`, `value`, `decision`, `input_evidence`로 설명 가능해야 한다.

## 6. 백테스트 재현성 계약

run은 dataset content hash, strategy graph hash, engine semantic version, parameter hash, fill policy, fee·slippage·latency model, deterministic seed, 시작·종료 시각을 고정한다. 사건 정렬 키는 `(knowledge_at, source_priority, stable_sequence)`다. `source_priority`는 `market_status=10, candle=20, trade=30, orderbook=40, ticker=50, order_update=60, risk=70`으로 고정한다. 호가 `stable_sequence`는 `source_receipts.id`를 사용해 A-B-A 수신을 보존한다. `connection_id`와 `frame_sequence`는 연결별 전달 provenance와 멱등성에만 사용하고 연결 간 순서에는 사용하지 않는다. 그 밖에는 거래소 sequence가 있으면 사용하고 없으면 `(source natural key, received_at, fetch_manifest_id)`의 정규화 hash 순서를 사용한다. 실행 중 wall clock과 임의 난수는 직접 사용하지 않는다.

## 7. 실시간 envelope

```json
{
  "schema_version": "1.0",
  "topic": "risk.event",
  "event_id": "01J2EXAMPLE000000000000000",
  "sequence": 1042,
  "cursor": "opaque-resume-cursor",
  "occurred_at": "2026-07-17T00:00:00Z",
  "published_at": "2026-07-17T00:00:00.120000Z",
  "scope": "operator:goodjoon",
  "message_type": "event",
  "payload": {}
}
```

`message_type`은 `subscribed|event|heartbeat|snapshot_required|slow_consumer|error`다. subscribe·unsubscribe 명령은 topic, scope, resume cursor를 가진다. `event_id`는 dedup key이며 sequence는 topic·권한 scope 안에서 단조 증가한다. cursor는 snapshot version과 마지막 sequence를 서명한 opaque value이며 최소 24시간 보존한다. 클라이언트가 gap·cursor expiry를 감지하면 REST snapshot을 읽기 전까지 이후 event를 적용하지 않는다. heartbeat는 마지막 sequence와 server time을 포함한다. 지원 schema major가 다르면 연결을 거부하고 minor·patch는 backward-compatible field 추가만 허용한다.

P2-7 구현은 `/v1/realtime/analysis/stream` 분석 스트림에 이 envelope를 적용하고 전환기 호환 alias로 `/v1/realtime/analysis`와 legacy top-level field를 유지한다. 새 구독은 `analysis.instrument:{instrumentId}:{unit}:{rangeDays}` topic의 `subscribed` sequence 1에서 시작하고 이후 `event`가 1씩 증가한다. `heartbeat`는 마지막 sequence를 반복한다. P2-8 구현은 `/v1/realtime/analysis/snapshot` REST endpoint가 같은 topic·scope의 상태 snapshot과 서명 cursor를 발급하게 한다. REST snapshot cursor의 snapshot version은 snapshot content hash를 포함한다. 브라우저는 중복·역순 event를 reducer에 전달하지 않고, gap·cursor 만료·`snapshot_required`·`slow_consumer` 이후 event 적용을 멈춘 뒤 REST snapshot으로 상태를 교체하고 `resumeCursor`로 같은 구독을 재개한다. 서버는 유효하고 현재 snapshotVersion과 같은 REST snapshot cursor 재구독에서 전체 초기 snapshot을 다시 보내지 않고 `subscribed`와 heartbeat 경계부터 이어간다. cursor 발급 뒤 snapshot 내용이 달라졌거나 기존 static cursor를 사용하면 최신 전체 snapshot을 다시 보내 상태 누락을 막는다. 위변조·문맥 불일치·만료 cursor와 지원하지 않는 snapshot version은 `snapshot_required`로 수렴한다.

## 8. Upbit 공식 제약

검증일: 2026-07-17, Upbit 개발자센터 v1.6.3

- 일반 Quotation REST `market`, `candle`, `trade`, `ticker`, `orderbook` 그룹은 각각 10 req/s/IP이며 같은 그룹 API가 한도를 공유한다. `Origin` header가 있는 Quotation REST·WebSocket은 10초당 1회이므로 server-to-server 요청에 Origin을 임의 추가하지 않는다.
- Exchange `default` 30 req/s/포켓, `order`·`order-test` 각 8 req/s/포켓, 전체 취소는 2초당 1회/포켓을 넘지 않는다. 같은 포켓의 여러 API Key는 한도를 공유한다.
- Gateway는 429·418을 자동 재전송하지 않고 원래 상태와 냉각 정보를 소비자에게 반환한다. 읽기 전용 수집기·스케줄러만 429 뒤 다음 초 경계까지 기다려 새로운 멱등 GET을 발행할 수 있고 반복 429는 circuit breaker로 격리한다. 주문·취소 POST는 자동 재시도하지 않으며 전송 결과가 불명확하면 `outcome_unknown`과 대사로 전환한다. 418은 응답 차단 시간 동안 해당 scope를 차단한다.
- WebSocket 연결은 5회/s이며 무인증은 IP, 인증 연결은 포켓 scope다. 연결 후 요청 message는 connection별 5회/s·100회/min이다. 120초 무송수신 종료를 막기 위한 ping과 재연결을 구현한다.
- 분봉 unit은 1, 3, 5, 10, 15, 30, 60, 240이며 REST count는 최대 200이다.
- 초봉 REST는 요청 시점 기준 최근 3개월만 제공하며 빈·부족 응답이 가능하다. 제품은 동적 cutoff 이전을 내부 `unavailable`로 매핑하고 이 결정의 근거를 manifest에 남긴다.
- 무체결 구간에는 candle이 생성되지 않는다. WebSocket candle은 변경 때만 전송되고 이전 candle이 initial snapshot으로 오거나 같은 시각이 중복될 수 있어 `(market, unit, candle_date_time)`로 idempotent last-write 한다.
- `myOrder`는 private endpoint와 JWT, `주문조회` 권한을 요구하고 initial snapshot 없이 실제 주문·체결 event만 보낸다. codes 생략·빈 배열은 전체 market, 지정 code는 대문자다. `trade_fee`, `is_maker` nullable, `prevented_volume`, `prevented_locked`를 보존하고 REST 초기·재연결 대사를 항상 수행한다.
- SMP는 taker 주문 기준의 선택 기능이다. `cancel_taker`는 신규 taker, `cancel_maker`는 기존 maker를 취소하고 `reduce`는 양쪽 수량을 줄인다. `post_only`와 `smp_type` 동시 사용을 거부하며 `state=prevented`와 prevented field를 보존한다.
- `POST /v1/orders/test`는 `주문하기` 권한과 독립 `order-test` 8 req/s/포켓을 사용하고 실제 주문·체결을 만들지 않는다. `market_offline`을 주문 불가로 처리한다. identifier는 최대 64자이며 재사용하지 않고 반환 UUID·identifier는 조회·취소에 사용하지 않는다.
- API Key당 허용 IP는 최대 10개다. JWT는 `access_key`, 매 요청 새 UUID `nonce`, HS512를 사용한다. parameter가 있으면 실제 순서를 보존한 URL encoding 전 query string의 SHA-512 `query_hash`를 넣는다. Secret Key는 Base64 decode하지 않고 POST는 JSON body를 사용한다.

## 9. 보안 불변 조건

- 출금 endpoint, 출금 scope, secret key 반환 API는 존재하지 않는다.
- `paper`, `shadow`, 자동 테스트 actor는 실제 주문 명령을 제출할 수 없다.
- live activation은 일반 bot update와 다른 권한·명령·감사 event를 사용한다.
- 로그와 audit metadata는 JWT, secret, query hash, Authorization header를 저장하지 않는다.
