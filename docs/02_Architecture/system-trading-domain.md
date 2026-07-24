# 시스템 트레이딩 도메인 설계(Module Design)

상태: 승인된 목표 설계(Accepted Target), P1·P2·P3·P4-3 기계 계약 구현 중
버전: 1.6.1
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
| 백테스트 UI | Backtest Lab | `/v1/backtest-runs/{backtestRunId}` REST 계약으로 저장된 성과·체결·산출물을 읽기 전용 조회 |
| 계좌 | `exchange_accounts` | `(exchange, account_alias)`; secret 저장 금지 |
| 포트폴리오 | `portfolios` | `(owner_id, name)`, 현재 자산·현금 projection 경계 |
| 포트폴리오 | `portfolio_policies` | `(portfolio_id, version)` |
| 포트폴리오 | `capital_allocations` | `(portfolio_policy_id, scope_type, scope_key)` |
| 봇 | `bot_definitions` | `(owner_id, name)` |
| 봇 | `bot_instances` | strategy version, portfolio policy, optional backtest run, `paper|shadow` execution mode |
| 봇 | `bot_state_transitions` | `(bot_instance_id, request_id)`, actor·reason·evidence append-only |
| 주문 | `order_intents` | `(bot_instance_id, idempotency_key)`, 위험 판정 전후 상태와 결정 입력 hash 보존 |
| 주문 | `exchange_orders` | `(order_intent_id, simulated_order_key)`, P5는 `paper|shadow` simulated order만 허용 |
| 주문 | `order_fills` | `(exchange_order_id, fill_sequence)`, paper simulator·shadow observation·reconciliation fill append-only |
| 포지션 | `position_projections` | `(portfolio_id, instrument_id)` 현재 projection, source fill provenance 보존 |
| 위험 | `risk_limits` | `(scope_type, scope_key, limit_type, version)` |
| 위험 | `risk_events` | `(scope_type, scope_key, fingerprint)`, 정책 version·evidence append-only |
| 긴급 정지 | `kill_switches` | `(scope_type, scope_key, sequence)`, `armed|released`와 open order 처리 정책 감사 |
| 대사 | `reconciliation_runs` | paper/shadow 내부 대사와 P6 live REST 대사 실행 증적의 중복 방지, mismatch/outcome_unknown 감사를 고정한다. live 적용 증적은 `upbit_live_reconciliation_applications`에서 binding 일치를 추가 검증한다. |
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

P6-2는 위 식별자 경계를 먼저 DB와 shared utility로 고정한다. `20260718000900_p6_order_identity_separation.sql`은 `exchange_accounts`, `upbit_order_identifier_reservations`, `live_order_identifiers`, `upbit_order_test_runs`를 추가한다. `upbit_order_test_runs`는 응답 UUID·identifier를 보존하지만 `lookup_allowed=false`, `cancel_allowed=false` DB 제약으로만 저장하고 UPDATE·DELETE를 거부하는 append-only 증적이다. `live_order_identifiers`는 `order_intents.idempotency_key`와 계좌 안정 식별자에서 DB 함수로 다시 계산한 `gm1_` identifier만 허용하고 계좌 안에서 영구 unique다. live/test 양쪽 AFTER trigger는 같은 계좌의 모든 live identifier와 order-test 응답 UUID·identifier를 `upbit_order_identifier_reservations`에 원자적으로 예약하므로 동시 transaction에서도 같은 식별자 재사용을 unique key로 거부한다. 이 단계는 아직 실제 주문 outbox나 `POST /v1/orders` 상향 호출을 만들지 않는다.

### 4.6 긴급 정지와 live capability

`trading_capabilities`는 global `live_disabled|live_enabled` 권위 상태와 승인 actor·reason·approved_at·expires_at·deployment_sha를 가진다. 조회 실패·불일치·만료·새 SHA는 `live_disabled`로 평가한다. `kill_switches`는 `global|bot|account` scope, `armed|released`, reason, actor와 sequence를 가진다. 주문 의도 승인 transaction은 capability와 모든 적용 switch를 잠그고 검사해 신규 주문과 race를 차단한다. switch arm 후 진행 주문은 정책에 따라 `leave_open|cancel_open`을 선택하고 결과를 감사한다.

P6-3은 `trading_capabilities`를 append-only PostgreSQL 권위 로그로 추가한다. 최신 global 행만 평가하며 행 없음, DB 조회 실패, `deployment_sha` 불일치, `expires_at` 만료, 명시 `live_disabled`는 모두 `live_disabled`로 닫는다. `ci:`, `ai:`, `service:` actor는 DB 제약으로 capability 기록을 만들 수 없다. 이 단계는 live 활성화 API나 실제 주문 제출을 만들지 않고, 후속 주문 adapter가 사용할 fail-closed guardrail만 제공한다.

P6-4는 private `myOrder` WebSocket event를 내부 대사 입력으로 정규화한다. Upbit `myOrder`는 initial snapshot 없이 실제 주문·체결 event만 보내므로 무이벤트는 정상 관측이며 REST snapshot 대사를 요구한다. `prevented_volume`, `prevented_locked`, nullable `trade_fee`, nullable `is_maker`를 보존하고, `state=trade`와 잔량이 있는 관측은 부분 체결로 분류한다. 모든 `myOrder` 대사 계획은 동일 주문 재제출을 금지한다.

P6-5는 주문조회 권한 기반 REST snapshot을 기존 내부 원장 대사 입력으로 변환한다. `GET /v1/order`, `GET /v1/orders/open`, `GET /v1/orders/closed`, `GET /v1/orders/uuids` 응답은 `docs/contracts/upbit/rest-order-reconciliation.md` 계약으로 정규화한다. `done|cancel|prevented|rejected` terminal snapshot만 기존 `reconcile_exchange_order()`에 적용하고, `wait|watch|trade` 진행 중 snapshot은 observe-only로 남긴다. 이 단계는 실제 REST client, 주문 제출·취소, private WebSocket 연결을 만들지 않는다. live 주문 적용은 P6-9의 binding 검증 계약을 추가로 통과해야 한다.

P6-6은 실제 주문 제출 전 outbox와 권한 준비도 경계를 고정한다. `upbit_api_key_permission_attestations`는 운영자가 주문하기와 주문조회 권한이 모두 있고 출금 권한이 없음을 증명한 append-only 행만 허용한다. `upbit_order_outbox`는 `live_order_identifiers`와 연결된 주문 의도를 `ready|blocked` 증적으로 저장하지만 `submit_attempt_count=0`을 DB 제약으로 고정한다. `ready` outbox는 승인 완료(`approved`) 주문 의도만 허용한다. 권한 증적을 참조하는 outbox는 `blocked` 상태라도 같은 `exchange_account_id`에 귀속돼야 한다. shared adapter는 live capability, 권한 만료, 출금 권한 존재, kill switch를 모두 fail-closed로 평가하며, `ready` outbox도 실제 제출 가능(`can_submit`)으로 해석하지 않는다. 실제 `POST /v1/orders` 호출과 submit worker는 후속 범위다.

P6-7은 Upbit live 주문 UUID·identifier와 내부 `exchange_orders` 결합 계약을 추가한다. `exchange_orders.execution_mode='live'`는 저장 계약에만 열리지만, `exchange_orders_require_live_binding` 지연 제약 트리거(deferrable constraint trigger)가 같은 트랜잭션 안의 `upbit_live_exchange_order_bindings` 결합 없이 커밋되는 live 주문 행을 거부한다. `upbit_live_exchange_order_bindings`는 `exchange_orders`, `live_order_identifiers`, `upbit_order_outbox`가 같은 거래소 계좌(exchange account)와 주문 의도(order intent)에 귀속되고 Upbit `identifier`가 내부 `gm1_` identifier와 일치할 때만 append-only 증적으로 저장된다. `upbit_order_uuid`는 표준 UUID 형식이어야 하며, order-test 응답 UUID·identifier는 live 결합에 사용할 수 없다. 결합 증적이 저장되면 `live_order_identifiers.status`는 `submitted`로 전이되지만, live 주문 대사 적용과 submit worker는 후속 범위다.

P6-8은 실제 주문 제출 전 리허설(rehearsal) 경계를 추가한다. `upbit_order_submit_rehearsals`는 `ready` outbox의 주문 payload와 hash를 그대로 참조하고, 공식 주문 생성 endpoint key(`rest.new-order`), method(`POST`), path(`/v1/orders`), query string, SHA-512 query hash를 append-only 증적으로 저장한다. DB는 outbox·live identifier·permission attestation의 계좌와 주문 의도 일치, reserved identifier, 만료되지 않은 permission, 기존 live binding 부재를 강제한다. 리허설은 `actual_request_sent=false`, `would_submit=false`, `can_bind_response=false`만 허용하고 응답 UUID·identifier를 저장할 수 없다. shared adapter는 payload 정규화와 hash 생성만 수행하며 실제 HTTP client나 `POST /v1/orders` 호출 경로를 추가하지 않는다. 실제 제출 worker는 후속 범위다.

P6-9는 결합된 Upbit live 주문에 REST terminal snapshot 대사를 적용하는 경계를 추가한다. `upbit_live_reconciliation_applications`는 `upbit_live_exchange_order_bindings`, `reconciliation_runs`, REST snapshot evidence의 UUID·identifier·state·source endpoint 일치를 DB에서 다시 검증한다. live `reconciliation_runs(status='succeeded')`는 같은 transaction 안의 application 증적 없이는 커밋될 수 없다. shared adapter는 binding snapshot과 이미 수신한 REST 주문 snapshot을 비교하고 terminal snapshot만 원자적 live 적용 store 메서드에 전달해 원장 대사와 live 적용 증적을 함께 남긴다. 진행 중 snapshot은 observe-only이며, 모든 경로는 `can_resubmit=false`, `actual_request_sent=false`, `actual_order_cancel_sent=false`를 유지한다. 이 단계도 실제 REST client, 주문 제출·취소, private WebSocket 연결을 추가하지 않는다.

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

P4-1은 `backtest-core-v1` 순수 엔진을 공유 모듈에 둔다. 이 엔진은 공통 전략 평가기(Common Strategy Evaluator)가 만든 신호를 이미 계산된 `BacktestSignal`로 받아 사건 재생·체결·성과 계산만 수행한다. 전략 연구, 백테스트, paper·shadow·live-ready는 같은 공통 전략 평가기와 주문 의미를 사용해야 하며, golden replay는 같은 입력 신호를 다시 주입했을 때 신호 동등성을 검증하는 기준이다. 호가가 없는 캔들 재생은 `orderbook_absent_uses_candle_close`, 부분 체결은 `partial_fill_by_candle_volume_participation` 가정을 결과에 남긴다. 기계 검증 계약은 [백테스트 엔진 계약](../contracts/backtest-engine.md)을 따른다.

P4-2 Backtest Store는 `backtest_runs`에 `input_hash`, `result_hash`, dataset content hash, strategy graph hash를 함께 저장한다. 저장 대상은 published 전략 버전과 sealed 데이터셋 버전으로 제한하며, 성공·실패·취소 terminal run과 `backtest_trades`, `backtest_equity_points`, `backtest_metrics`, `backtest_artifacts` 결과 행은 append-only로 봉인한다.

P4-4 Backtest Store 목록은 offset 대신 ID 기반 keyset pagination을 사용한다. 첫 페이지에서 `backtest_runs.id`의 최대값을 `ceiling`으로 고정하고, 다음 페이지는 `id <= ceiling AND id < lastId ORDER BY id DESC` 조건으로 읽는다. cursor는 `backtest-run-list-v1` 문맥, `ceiling`, `lastId`, HMAC digest를 담은 불투명 값이며 변조·문맥 불일치는 `BACKTEST_CURSOR_CONTEXT_MISMATCH`로 수렴한다. 목록 API는 `BacktestRunSummary`만 반환하고 체결·equity·metric payload와 artifact 상세는 단건 조회 또는 후속 대용량 pagination 계약으로 분리한다.

P4-5 Backtest Worker는 저장된 `backtest_runs`의 worker queue 상태만 처리한다. `PostgresBacktestStore.claim_next_run()`은 `FOR UPDATE SKIP LOCKED`와 `lease_generation`으로 run 하나를 임대하고, `complete_claimed_run()`과 `fail_claimed_run()`은 owner·generation·미만료 lease를 검증해 늦은 worker의 결과·artifact 쓰기를 차단한다. 실패는 시도 예산이 남으면 `retry_wait`, 소진하면 `dead_letter`로 전이한다. `BacktestWorker`는 claim된 run을 주입된 executor에 전달하고, executor가 반환한 `BacktestResult`와 artifact를 같은 DB transaction 경계 안에서 terminal 결과로 저장한다. P4-5는 실행 생성 API와 strategy/dataset replay materialization을 확장하지 않는다.

P4-6 Backtest Result pagination은 단건 run 상세의 호환 payload를 유지한 채 대용량 체결과 자산곡선을 별도 endpoint로 분리한다. `Backtest Store`는 `trade_sequence`와 `point_sequence` 오름차순 keyset pagination을 사용하고 첫 페이지의 최대 sequence를 cursor `ceiling`에 고정한다. cursor는 `backtestRunId`, `ceiling`, `lastSequence`, HMAC digest를 포함하며 다른 run 문맥에서 재사용하면 `BACKTEST_RESULT_CURSOR_CONTEXT_MISMATCH`로 수렴한다.

P4-7 Backtest 실행 생성 API는 `POST /v1/backtest-runs` 명령을 운영 토큰으로 보호하고 `202 Accepted`와 `BacktestRunSummary`를 반환한다. 생성은 published 전략 version과 sealed 데이터셋 version만 허용하며, 같은 transaction에서 strategy graph hash, dataset content hash, dataset as-of·범위·fill/missing policy, engine version, canonical parameters hash, initial cash, execution model, deterministic seed, materialized candle events를 `backtest-run-input-v1` payload로 고정한다. `input_hash`는 이 payload를 사용한 백테스트 엔진 input hash와 같아야 하며, payload는 `backtest_runs.input_payload`에 보존해 Worker claim이 동일 입력을 재구성할 수 있게 한다. 새 run은 내부 상태 `queued`, 외부 상태 `pending`, `result_hash=NULL`로 저장되고 기존 Worker lease 흐름의 입력이 된다. 같은 idempotency key와 같은 본문은 기존 run을 재생하고, 같은 key의 다른 본문은 `BACKTEST_IDEMPOTENCY_CONFLICT`로 거부한다. 같은 semantic input hash가 이미 있으면 다른 idempotency key의 중복 생성으로 보고 409로 거부한다.

P4-8 Backtest 성과 artifact는 `walk_forward_summary`, `sensitivity_summary`, `bootstrap_summary`를 Backtest Store의 기존 `backtest_artifacts` 입력 형태로 생성한다. 각 artifact metadata는 schema version(`backtest-artifact-walk-forward-v1`, `backtest-artifact-sensitivity-v1`, `backtest-artifact-bootstrap-v1`), `inputHash`, `resultHash`, 정렬된 분석 행, `finalEquity` min/max/mean 요약을 포함한다. `contentHash`는 metadata 정규 JSON SHA-256으로 계산해 입력 순서와 dictionary 삽입 순서가 결과 식별자에 영향을 주지 않게 한다.

P4-9 Backtest progress WebSocket은 `ws://<api-host>/v1/backtest-runs/{backtestRunId}/progress`에서 현재 `BacktestRunSummary` 기반 snapshot을 전송한다. P4-9는 별도 progress row를 만들지 않고 run 상태에서 `pending=0`, `running=50`, terminal=100 진행률을 파생한다. 없는 run은 연결 수락 뒤 `backtest.error`와 `BACKTEST_RUN_NOT_FOUND`를 보내고 닫는다.

P5-1은 paper/shadow 실행 연결 전에 영속화 경계를 먼저 고정한다. `20260718000500_p5_portfolio_bot_risk.sql`은 portfolio policy, bot instance, order intent, simulated exchange order, order fill, position projection, risk limit/event, kill switch를 PostgreSQL 계약으로 추가한다. P5-1의 `execution_mode`는 `paper|shadow`만 허용하고 `bot_instances.stage`는 `draft|backtest|paper|shadow|paused|stopped|faulted`까지만 허용한다. 실제 Upbit 주문 제출, private WebSocket, 주문 테스트 API와 live-ready/live 전이는 P6 이후 범위다.

P5-2는 포트폴리오 API 명령 경계를 연다. `20260718000600_p5_portfolio_api_commands.sql`은 API로 생성된 `portfolios` 행에 `request_id`, `idempotency_key`, `requested_at`, `request_hash`를 보존하고, 멱등 키 부분 고유 인덱스(partial unique index)와 all-or-none 제약으로 기존 fixture·수동 행과 API 명령 행을 구분한다. `POST /v1/portfolios`는 운영자 토큰과 멱등 명령을 요구하고, 동일 멱등 키의 다른 본문은 `PORTFOLIO_IDEMPOTENCY_CONFLICT`로 거부한다. `GET /v1/portfolios` cursor는 owner와 최초 ID 상한을 고정한다.

P5-3은 실제 Upbit 주문 제출 없이 `paper_execution_jobs` 큐만 처리한다. `20260718000700_p5_paper_execution_jobs.sql`은 approved `order_intents`를 `paper` 실행 모드의 임대 가능한 작업으로 연결하고, `FOR UPDATE SKIP LOCKED`, lease owner, lease expiry, lease generation, retry/dead-letter 상태로 중복 실행과 늦은 worker 쓰기를 차단한다. `PaperExecutionWorker`는 claim된 job을 주입된 paper simulator에 전달하고, completion transaction에서 simulated `exchange_orders`, `paper_simulator` fill, `position_projections`, `order_intents.status='paper_filled'`를 함께 기록한다. 이 worker는 private WebSocket, 주문 테스트 API, 실제 주문 submit/cancel/read 경로를 호출하지 않는다.

P5-4 Risk Worker는 새 DB migration 없이 P5-1의 `order_intents`, `risk_limits`, `risk_events`, `kill_switches`와 P5-3의 `paper_execution_jobs` 계약을 소비한다. `RiskEvaluationWorker`는 `created` 주문 의도를 `FOR UPDATE SKIP LOCKED`로 하나만 잠그고 전역·포트폴리오·봇·거래쌍 위험 한도와 전역·포트폴리오·봇 kill switch 최신 sequence를 같은 transaction에서 평가한다. 활성 kill switch가 있거나 적용 가능한 한도를 초과하거나 필요한 계산 증적이 없으면 `risk_rejected`와 `kill_switch_rejected|limit_rejected` 이벤트를 append-only로 남긴다. 승인된 paper 주문 의도는 같은 transaction에서 `approved`, `policy_approved`, `paper_execution_jobs` pending으로 연결한다. `PaperExecutionWorker`의 claim과 completion은 활성 kill switch를 다시 검사해 arm 이후 신규 모의 체결 생성을 막는다. P5-4는 private WebSocket, 주문 테스트 API, 실제 Upbit 주문 submit/cancel/read 경로를 호출하지 않는다.

P5-5 reconciliation은 private 계좌 조회 없이 paper/shadow 내부 주문·체결 원장과 포지션 projection을 대사한다. `20260718000800_p5_reconciliation_runs.sql`은 `exchange_orders`별 대사 run key, request hash, 관측 상태, 관측 fill count, actor·reason·evidence를 append-only로 저장한다. `PostgresPortfolioBotStore.reconcile_exchange_order()`는 대상 `exchange_orders`와 `order_intents`를 잠그고, 새 reconciliation fill만 `order_fills(fill_source='reconciliation')`에 append한 뒤 같은 transaction에서 `position_projections`를 갱신한다. 동일 run key와 동일 request hash는 멱등으로 흡수하고, 같은 fill sequence의 기존 fill과 관측 fill이 다르면 position을 바꾸지 않고 `reconciliation_mismatch` 위험 이벤트를 남긴다. 주문 결과가 충분히 확인되지 않은 관측은 `outcome_unknown`으로 남기며 동일 주문 재제출을 허용하지 않는다. P5-5는 private WebSocket, 주문 테스트 API, 실제 Upbit 주문 submit/cancel/read 경로를 호출하지 않는다.

P5-6 Bot Workshop은 P5 저장소·worker 상태의 운영 흐름을 UI에서 읽기 전용으로 묶는다. Operations Console은 Bot Workshop 메뉴와 `REST 준비` 갱신 기준을 제공하고, 화면은 Portfolio allocation, 봇 승격 단계, order intent부터 position projection까지의 paper/shadow 파이프라인, global kill switch, 승인 checklist, reconciliation mismatch/outcome_unknown 증적을 표시한다. live_ready/live는 안전 잠금 상태로 표현하며 일반 UI action으로 활성화하지 않는다. P5-6은 새 DB/API 계약을 만들지 않고 실제 Upbit 주문 submit/cancel/read, private WebSocket, 주문 테스트 API 경로를 호출하지 않는다.

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

검증일: 2026-07-18, Upbit 개발자센터 v1.6.3

- 일반 Quotation REST `market`, `candle`, `trade`, `ticker`, `orderbook` 그룹은 각각 10 req/s/IP이며 같은 그룹 API가 한도를 공유한다. `Origin` header가 있는 Quotation REST·WebSocket은 10초당 1회이므로 server-to-server 요청에 Origin을 임의 추가하지 않는다.
- Exchange `default` 30 req/s/포켓, `order`·`order-test` 각 8 req/s/포켓, 전체 취소는 2초당 1회/포켓을 넘지 않는다. 같은 포켓의 여러 API Key는 한도를 공유한다.
- Gateway는 429·418을 자동 재전송하지 않고 원래 상태와 냉각 정보를 소비자에게 반환한다. 읽기 전용 수집기·스케줄러만 429 뒤 다음 초 경계까지 기다려 새로운 멱등 GET을 발행할 수 있고 반복 429는 circuit breaker로 격리한다. 주문·취소 POST는 자동 재시도하지 않으며 전송 결과가 불명확하면 `outcome_unknown`과 대사로 전환한다. 418은 응답 차단 시간 동안 해당 scope를 차단한다.
- WebSocket 연결은 5회/s이며 무인증은 IP, 인증 연결은 포켓 scope다. 연결 후 요청 message는 connection별 5회/s·100회/min이다. 120초 무송수신 종료를 막기 위한 ping과 재연결을 구현한다.
- 분봉 unit은 1, 3, 5, 10, 15, 30, 60, 240이며 REST count는 최대 200이다.
- 초봉 REST는 요청 시점 기준 최근 3개월만 제공하며 빈·부족 응답이 가능하다. 제품은 동적 cutoff 이전을 내부 `unavailable`로 매핑하고 이 결정의 근거를 manifest에 남긴다.
- 무체결 구간에는 candle이 생성되지 않는다. WebSocket candle은 변경 때만 전송되고 이전 candle이 initial snapshot으로 오거나 같은 시각이 중복될 수 있어 `(market, unit, candle_date_time)`로 idempotent last-write 한다.
- `myOrder`는 private endpoint와 JWT, `주문조회` 권한을 요구하고 initial snapshot 없이 실제 주문·체결 event만 보낸다. codes 생략·빈 배열은 전체 market, 지정 code는 대문자다. `trade_fee`, `is_maker` nullable, `prevented_volume`, `prevented_locked`를 보존하고 REST 초기·재연결 대사를 항상 수행한다.
- SMP는 taker 주문 기준의 선택 기능이다. `cancel_taker`는 신규 taker, `cancel_maker`는 기존 maker를 취소하고 `reduce`는 양쪽 수량을 줄인다. P6-1 Gateway 계약과 validator는 `post_only`와 `smp_type` 동시 사용을 상향 호출 전 거부한다. 후속 private 주문 대사는 `state=prevented`와 prevented field를 보존해야 한다.
- `POST /v1/orders/test`는 `주문하기` 권한과 독립 `order-test` 8 req/s/포켓을 사용하고 실제 주문·체결을 만들지 않는다. `market_offline`을 주문 불가로 처리한다. P6-1 Gateway 계약과 validator는 identifier 최대 64자를 상향 호출 전 검사한다. 반환 UUID·identifier는 조회·취소에 사용하지 않는다.
- API Key당 허용 IP는 최대 10개다. JWT는 `access_key`, 매 요청 새 UUID `nonce`, HS512를 사용한다. parameter가 있으면 실제 순서를 보존한 URL encoding 전 query string의 SHA-512 `query_hash`를 넣는다. Secret Key는 Base64 decode하지 않고 POST는 JSON body를 사용한다.

P6-4는 `myOrder` 연결을 추가하지 않고 event parser와 내부 대사 입력 계약만 고정한다. 무이벤트는 정상 상태로 보고 REST snapshot 대사를 요구하며, `state=prevented`와 부분 체결은 필드를 보존한 관측 상태로 분류한다. 모든 대사 계획은 `can_resubmit=false`로 동일 주문 재제출을 금지한다.

## 9. 보안 불변 조건

- 출금 endpoint, 출금 scope, secret key 반환 API는 존재하지 않는다.
- `paper`, `shadow`, 자동 테스트 actor는 실제 주문 명령을 제출할 수 없다.
- live activation은 일반 bot update와 다른 권한·명령·감사 event를 사용한다.
- 로그와 audit metadata는 JWT, secret, query hash, Authorization header를 저장하지 않는다.
