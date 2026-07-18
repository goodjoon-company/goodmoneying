# 시스템 트레이딩 플랫폼 아키텍처(Architecture)

상태: 승인됨(Accepted)

최종 갱신: 2026-07-18

## 1. 목적과 경계

이 문서는 goodmoneying의 시스템 경계, 모듈 책임, 데이터 흐름, 배포 구조를 정의하는 아키텍처 색인(Architecture Index)이다. 제품 정책은 [제품 요구사항](01_Product.md), DB·HTTP·WebSocket·상태 기계의 세부 구조는 [계약](contracts/README.md)을 단일 기준으로 사용한다. DB 변경의 단일 기준은 `docs/contracts/db/migrations/`이며 런타임(Runtime) API·worker는 DDL을 실행하지 않는다.

현재 플랫폼은 업비트 가상자산만 실행한다. 거래소 어댑터 경계는 유지하지만 주식·타 거래소 구현은 포함하지 않는다. PostgreSQL을 데이터·내구성 작업 큐의 기본 저장소로 사용하며 측정 근거 없이 별도 브로커나 데이터베이스를 도입하지 않는다.

## 2. 아키텍처 원칙

1. **하나의 전략 의미**: 연구, 백테스트, 모의, 그림자, 라이브 준비는 같은 전략 그래프 평가기와 주문 상태 모델을 사용한다.
2. **원천 불변성과 파생 추적성**: 원천 행은 관측 사실만 저장하고 집계·지표·채움은 버전이 있는 파생 계층에서 수행한다.
3. **시간 인과성**: 저장은 UTC, 표시는 KST다. 각 평가 시각 이후 도착하거나 이후에 알려진 데이터 접근을 계약과 테스트로 막는다.
4. **Decimal 전 구간**: 가격·수량·금액·수수료·손익은 DB `numeric`과 Python `Decimal`, JSON 문자열 표현을 사용한다.
5. **멱등 명령**: 수집·백필·전략 실행·주문 의도는 자연키 또는 멱등 키로 중복 실행을 흡수한다.
6. **기본 거부(Default Deny)**: 실거래, private 계좌 데이터, 위험 한도 변경은 명시 권한과 감사 사유 없이는 거부한다.
7. **유실 가능한 스트림, 복구 가능한 상태**: WebSocket은 상태의 단일 기준이 아니며 시퀀스 유실 시 REST 스냅숏과 커서로 복구한다.
8. **수직 기능 조각**: 계약, DB, 도메인, API, UI, E2E와 운영 증적을 한 사용자 결과 단위로 완성한다.

## 3. 현재 준수 상태

이 문서는 승인된 목표 아키텍처다. `Accepted`는 구현 완료나 운영 강제를 의미하지 않는다. 구현 상태는 다음과 같으며 미구현 배포 gate가 하나라도 있으면 P8 승격을 차단한다.

| 영역 | 2026-07-18 기준 | 구현 추적 |
|---|---|---|
| 기존 수집·백필·집계, API·Web, Upbit Lab·Gateway | 부분 구현 | Issue #28, #29, #33 |
| 전략·백테스트·포트폴리오·봇·주문·위험 | P3 전략, P4 백테스트, P5-1 portfolio/bot/order intent/risk DB 계약, P5-2 portfolio API/Store, P5-3 paper execution queue/worker, P5-4 risk evaluation worker와 kill switch 차단, P5-5 paper/shadow 내부 reconciliation 증적과 projection 갱신, P5-6 Bot Workshop UI, P6-2 order-test 증적과 live 주문 identifier 분리 구현 | Issue #30~#33 |
| 내부 UTC와 5가지 품질 상태 | 미구현 | Issue #28 |
| 복구 가능한 내부 WebSocket | P2-7 envelope·cursor·heartbeat·gap 적용 중단, P2-8 REST snapshot 복구·slow consumer 신호 구현 | Issue #29 |
| Action commit SHA pinning·P8 exact-SHA 잠금 | P0 구현, 배포는 계속 차단 | Issue #27, #35 |
| branch protection·prod 승인·승격 자동화 | 미구현, 배포 차단 | Issue #35 |
| 운영 DB 백업·복원 rehearsal·forward recovery | 미구현, 배포 차단 | Issue #34, #35 |
| 전체 worker·row delta·WebSocket·SHA health gate | 미구현, 배포 차단 | Issue #35 |
| 감사 가능한 global `live_disabled` 권위 상태 | 미구현, live 차단 | Issue #32, #33 |

## 4. 목표 시스템 구성

| 구성 요소 | 책임 | 상태 저장 | 현재 상태 |
|---|---|---|---|
| Web | 연구·전략·백테스트·봇·위험·운영 UI, REST 부트스트랩, WebSocket 구독 | 브라우저 단기 상태만 유지 | P5-6 Bot Workshop까지 부분 구현 |
| API | 권한, 명령·조회, 버전 생성, 스냅숏 복구, 내부 이벤트 발행 | PostgreSQL | 부분 구현 |
| Upbit Gateway | 공식 REST·WebSocket, JWT, 요청 제한, SMP, 주문 테스트와 실제 주문 안전 경계 | 비밀 파일은 프로세스 외부, 추적 메타데이터만 DB | P6-1 주문 identifier 64자와 `post_only`+SMP 로컬 거부 구현, 실제 주문 adapter와 private 대사는 미구현 |
| Market Sync Worker | 전체 거래쌍과 상태 이력 동기화, KRW 기본 정책 자동 편입 | PostgreSQL | 실시간 worker에 일부 결합 |
| Realtime Worker | 체결·호가·티커·캔들 구독, 중복 제거, 원천 저장, 연결 복구 | PostgreSQL | 부분 구현 |
| Backfill Scheduler/Worker | 커버리지 격차에서 작업 생성, 임대, 재시도, 200개 단위 역방향 백필 | PostgreSQL 큐 | 수동 흐름 부분 구현 |
| Quality Worker | 구간 상태, 지연, 중복, 실패, 획득 불가 판정 | PostgreSQL | 미구현 |
| Rollup Worker | 1분·일 원천 개정 기반 11개 주기 UTC 집계와 계보 저장 | PostgreSQL | P2-1 구현 |
| Indicator Worker | 버전 지표·시장 통계·1분 미시구조 통계의 무효화 처리와 불변 물질화(Materialization) | PostgreSQL | P2-3·P2-4 구현 |
| Strategy Worker | Typed DAG 검증·평가·설명 이벤트 생성 | PostgreSQL | 미구현 |
| Backtest Worker | 결정론적 사건 재생, 체결·비용 모델, 성과·산출물 생성 | PostgreSQL | P4 구현 |
| Paper/Shadow Worker | 모의 체결 또는 실시간 신호 관찰, 실제 주문 금지 | PostgreSQL | P5-3 paper execution worker 구현, shadow worker 미구현 |
| Bot Worker | 승인 버전 실행, 주문 의도 생성, 상태 전이 | PostgreSQL | 미구현 |
| Reconciliation Worker | paper/shadow 내부 체결 원장과 포지션 projection을 대사하고, P6 이후 REST·private WebSocket·잔고 대사로 확장 | PostgreSQL | P5-5 내부 대사 증적 구현, private 계좌 대사는 P6 이후 |
| Risk Worker | 사전 주문·실시간 노출 검사, 위험 이벤트, 긴급 정지 | PostgreSQL | P5-4 created 주문 의도 평가, 위험 이벤트, kill switch 신규 주문·paper job 차단 구현 |
| Operations Worker | 하트비트, 큐·DB·요청 제한·배포 상태와 알림 | PostgreSQL | 부분 구현 |

작업자는 하나의 실행 바이너리에서 역할별 프로세스로 시작할 수 있다. 배포 단위 분리는 처리량·장애 격리 측정으로 결정하며 도메인 계약은 프로세스 배치와 독립적이다.

## 5. 계층과 의존 방향

```text
apps/web
  ↓ HTTP + WebSocket contracts
apps/api ───────────────→ apps/upbit_gateway
  ↓ application services          ↓ official Upbit API
packages/shared/goodmoneying_shared/domain  # 목표 경로, Issue #28부터 생성
  ↓ repositories + event outbox
PostgreSQL ← worker role processes
```

- 도메인 모델은 FastAPI, React, Upbit SDK에 의존하지 않는다.
- 애플리케이션 서비스는 저장소·거래소·시계 추상화에 의존한다.
- Upbit Gateway는 공식 API 응답을 내부 거래소 계약으로 번역하지만 전략·위험 정책을 결정하지 않는다.
- UI는 DB 구조를 직접 알지 않고 OpenAPI·WebSocket 계약만 소비한다.

## 6. 데이터 계층

### 6.1 원천 계층

원천 계층은 시장 상태, 시세, 계좌·주문·체결처럼 외부에서 관측한 사실과 수신 provenance를 불변에 가깝게 보존한다. 목표 엔터티와 자연키는 [도메인 설계](02_Architecture/system-trading-domain.md), 구현된 컬럼·제약은 DB migration을 단일 기준으로 사용한다.

### 6.2 제어·품질 계층

제어·품질 계층은 어떤 데이터를 왜·언제 수집했고 무엇을 얻지 못했는지 설명하며 정책, 대상, 실행, 작업, 커버리지, 품질 사건과 요청 manifest를 분리한다. PostgreSQL 작업 큐의 목표 임대·재시도 의미는 도메인 설계가 정의하고 실제 컬럼·인덱스는 migration이 정의한다.

### 6.3 집계·지표 계층

집계 계층은 최신 조회 투영 `source_candles`와 추가 전용 개정 원장 `source_candle_revisions`를 분리한다. 집계 결과는 계산 버전, 입력 개정 ID·내용 해시, `source_as_of`, `knowledge_at`, 5단계 품질과 완전성을 기록한다. `no_trade` 커버리지는 빈 슬롯을 완전하게 만들 수 있지만 미판정·누락 상태는 불완전하다. 원천 수정의 영향 구간 재계산은 P2-2 변경 전파가 같은 트랜잭션의 신규 개정 목록을 입력으로 사용한다.

P2-3 지표 계층은 SMA20·SMA60·EMA20·볼린저 밴드20·RSI14의 정의 버전과 해시를 고정하고, 원천/집계 개정 프런티어(frontier), 지식 시각, 부모 물질화 계보와 재개 체크포인트를 가진 추가 전용 결과를 저장한다. 시장 통계는 지표 정의에 종속시키지 않고 수익률·20구간 실현 변동성·거래량·거래대금을 독립 버전으로 저장한다. REST와 분석 WebSocket은 저장된 물질화만 읽으며 페이지 커서는 최초 조회의 ID 상한·상품·주기·범위를 고정한다. 원천·집계·1분 품질 변화는 내구성 무효화 큐를 만들고 집계 워커의 공정한 유휴 슬롯에서 지표 워커가 512구간씩 처리한다. 상품·주기는 무효화 ID와 불변 프런티어 순서로 직렬화한다. 인접 체크포인트의 정상 정정·추가는 영향 범위만 저장하고, 체크포인트가 없거나 떨어진 복구·정의 초기화는 재생 예열도 bounded 물질화해 부모·값·현재 입력 계보를 보존한다. 각 청크의 물질화·진행 체크포인트·큐 완료는 같은 fenced 트랜잭션이다. 상세 테이블과 wire 형식은 `docs/contracts/`를 따른다.

P2-4 미시구조 계층은 체결·호가 receipt와 정규화 원천을 직접 연결하고 닫힌 1분 구간을 별도 추가 전용 물질화로 저장한다. 호가는 같은 구간의 마지막 기본 단위 스냅숏, 체결은 매수(`BID`)·매도(`ASK`) 표시 원천을 사용하며 메이커·테이커 의미를 추론하지 않는다. 계산 상태와 5단계 원천 품질을 분리하고, 원천 캔들 개정과 체결량·거래대금을 대사해 실시간 체결 누락을 숨기지 않는다. 수집 트랜잭션은 receipt·정규화 행·더티 범위를 함께 확정하고, 워커는 이벤트마다 결과를 만들지 않고 상품별 1분 범위를 합쳐 닫힌 구간과 늦은 입력 정정만 불변 물질화한다. REST와 분석 WebSocket은 PostgreSQL 저장 결과만 읽고 SQLite는 빈 테스트 호환 투영만 제공한다.

P2-5 데이터셋 계층은 변경 가능한 빌드 수명주기와 게시 후 불변인 내용 주소 버전을 분리한다. 생성 요청 수락 시 반복 읽기 트랜잭션에서 `asOf`와 원천·품질·물질화·시장 상태 ceiling을 고정하고, worker는 그 프런티어 안의 정확한 candle·지표·시장 통계·미시구조 member와 시점별 시장 상태를 원자적으로 게시한다. 게시 작업은 임시 스테이징과 4,096행 서버 커서 증분 해시, 집합 기반 삽입으로 메모리를 제한하며 별도 연결 heartbeat와 소유자·세대·만료 시각 fencing으로 긴 작업의 단일 소유권을 보장한다. 실패는 제한된 지수 지연 재시도 뒤 dead-letter로 격리한다. canonical content hash는 DB 대리키 대신 시장 자연키·범위·정책·계산 버전·정렬된 member 내용으로 계산한다. 이후 늦은 과거 정정과 신규 수집은 기존 version을 바꾸지 않는다. 채움은 분석 계층의 `none` 또는 확정 `no_trade` candle 전용 정책이며 원천을 변경하지 않는다. REST는 저장된 build/version/coverage/series만 읽는다.

P2-6 Data Lab은 P2-5 REST 계약을 전용 화면으로 소비한다. 화면은 브라우저가 운영 토큰을 보관하지 않는 동일 출처 프록시를 사용하고, `/v1/data-foundation`의 `instrumentId`로 KRW 시장을 선택해 새 dataset build 명령을 만든다. build 목록은 `/v1/dataset-builds`를 5초 REST polling으로 재발견하며, version 목록·coverage heatmap·exact member 표와 차트·A/B 비교는 저장된 version과 series만 읽는다.

P2-7 내부 분석 WebSocket은 신규 `/v1/realtime/analysis/stream` payload를 `P2 envelope v1`로 감싼다. 기존 `/v1/realtime/analysis`는 전환기 호환 alias다. 각 frame은 `topic`, `scope`, `event_id`, `sequence`, 서명된 `cursor`, `message_type`, `payload`를 포함하며, 전환기 호환을 위해 legacy `version/type/sentAt` top-level 필드도 남긴다. 서버는 topic·scope·sequence·snapshot version·만료 시각을 cursor에 서명하고 heartbeat에 마지막 sequence와 server time을 실어 보낸다. Web 클라이언트는 envelope를 우선 해석하고 중복·역순 event를 버리며, sequence gap·서버 `snapshot_required`·`slow_consumer`를 받으면 이후 event를 reducer에 적용하지 않고 `/v1/realtime/analysis/snapshot` REST snapshot으로 상태를 교체한 뒤 `resumeCursor`로 재구독한다.

### 6.4 연구·실행 계층

연구·실행 계층은 사용한 시장·구간·원천 manifest·결측 정책을 불변 버전으로 고정하고 전략부터 주문·위험까지 같은 실행 의미를 공유한다. 목표 상태와 불변 조건은 [도메인 설계](02_Architecture/system-trading-domain.md)를 따르고 각 수직 조각의 실제 DB·API·메시지 형식은 `docs/contracts/`에 기계 계약으로 추가한다. P4-1 백테스트 코어는 [백테스트 엔진 계약](contracts/backtest-engine.md)을 기준으로 순수 공유 모듈에서 먼저 구현됐고, Worker·DB·API·Backtest Lab은 후속 P4 조각에서 이 경계를 소비한다.

## 7. 핵심 흐름

### 7.1 수집 정책 활성화

1. Market Sync Worker가 공식 거래쌍을 동기화하고 상태 이력을 닫힌·열린 구간으로 기록한다.
2. KRW 기본 정책이 신규 거래쌍을 `collection_targets`에 멱등 삽입한다.
3. 정책 변경 트랜잭션이 커버리지 격차와 `backfill_jobs`를 만들고 실시간 구독 desired state를 갱신한다.
4. Backfill Worker는 요청 제한 토큰을 얻고 최대 200개 캔들을 역순 수집한다.
5. Realtime Worker는 원하는 대상 집합 변화에 따라 구독을 갱신한다.
6. Quality Worker는 실제 캔들, 거래 이벤트, 시장 상태와 fetch manifest를 사용해 5가지 구간 상태를 판정한다.
7. 재시작 시 DB desired state와 만료 lease를 읽어 자동 복구한다.

### 7.2 전략 연구와 백테스트

1. 사용자가 Data Lab에서 KRW 시장과 KST 범위를 입력해 새 데이터셋 build를 만들거나 기존 데이터셋 버전을 복제한다.
2. Data Lab은 REST polling으로 build 수명주기를 재발견하고, 게시된 version의 coverage와 exact member를 표·차트로 비교한다.
3. Strategy Studio가 그래프 초안을 편집하고 서버 검증 결과를 실시간 표시한다.
4. 게시 명령은 서버 검증을 통과한 정규화된 그래프 해시로 불변 전략 버전을 생성한다.
5. Backtest Worker는 평가 시계가 허용한 사건만 순서대로 재생하고 전략 신호를 주문 의도로 변환한다.
6. 공통 주문 모델이 수수료·슬리피지·지연·부분 체결을 적용한다.
7. 결과와 모든 가정, 엔진 버전, seed를 저장하고 UI에 진행 이벤트를 보낸다.

### 7.3 봇과 주문 안전 흐름

1. 봇은 승인된 전략 버전·계좌·자금·위험 정책을 참조한다.
2. 전략 신호는 먼저 전역 멱등 키를 가진 주문 의도가 되고 Risk Worker가 같은 트랜잭션 경계에서 검사해 `risk_rejected` 또는 `approved`로 전이한다. 거부된 의도도 사유·정책 버전과 함께 감사·재현을 위해 보존한다.
3. `paper`는 결정론적 체결기, `shadow`는 실시간 평가만 사용하고 거래소 주문을 호출하지 않는다.
4. `live-ready`는 Upbit 주문 테스트 API와 private WebSocket·REST 대사를 검증하지만 실제 주문을 보내지 않는다.
5. `live` 전이는 운영자 권한, 활성화 사유, 모든 승인 gate와 전역 kill switch 해제 상태를 요구한다.
6. 주문 제출은 멱등 키와 outbox를 기록한 뒤 Upbit Gateway를 호출한다. 시간 초과는 즉시 재주문하지 않고 조회·대사 상태로 전환한다.
7. private WebSocket 이벤트는 빠른 상태 갱신에 쓰되 REST 대사가 최종 누락을 복구한다.
8. 전역 또는 봇 kill switch는 신규 주문 의도를 거부하고 진행 중 주문의 처리 정책을 감사 이벤트로 남긴다.

## 8. WebSocket 복구 모델

내부 메시지 envelope, 구독 명령, schema 호환성의 목표 의미는 [도메인 설계](02_Architecture/system-trading-domain.md#7-실시간-envelope)가 정의한다. 구현 시점의 기계 계약은 `docs/contracts/api/internal-realtime-stream.schema.json`과 `docs/contracts/api/openapi.yaml`의 `/v1/realtime/analysis/snapshot`이다. 연결은 구독 권한을 확인하고 현재 cursor를 회신한다. 클라이언트는 sequence gap, cursor 만료, `snapshot_required`, `slow_consumer`를 감지하면 해당 토픽을 일시 중지하고 REST 스냅숏(snapshot)을 cursor와 함께 다시 받은 뒤 같은 WebSocket에 `resumeCursor`로 재구독한다.

서버는 WebSocket 전송을 timeout guard로 감싸고 느린 소비자를 `stream.slow_consumer` 사유로 종료한다. 클라이언트는 이후 REST snapshot을 기준으로 상태를 교체하므로 무제한 event 적재나 gap 이후 reducer 적용을 허용하지 않는다.

## 9. 시간·결측·재현성

- `occurred_at`: 거래소가 사건을 발생시킨 UTC 시각
- `received_at`: 플랫폼이 원천을 수신한 UTC 시각
- `stored_at`: DB 커밋 UTC 시각
- `knowledge_at`: 백테스트에서 해당 사실을 사용할 수 있게 된 UTC 시각

백테스트 평가 시점 `t`는 `knowledge_at <= t`인 데이터만 볼 수 있다. 거래쌍 상태 역시 당시 유효 구간만 조회한다. 무거래 캔들은 원천에 생성하지 않고 `coverage_intervals=no_trade`로 표현한다. 분석 채움은 데이터셋·전략 버전 입력이며 원천을 변경하지 않는다.

## 10. 보안 경계

- 공개 시세와 private 계좌·주문 연결을 별도 세션·요청 제한 포켓으로 관리한다.
- API Key 파일은 운영 호스트 외부 비밀 경로에서 읽고 로그·DB·브라우저로 반환하지 않는다.
- Gateway allowlist는 호출 주체와 명령 종류를 제한한다.
- 주문하기와 주문조회 권한은 준비 상태에서 각각 검사한다. 출금 권한은 거부한다.
- 모든 변경 명령은 raw token이 아닌 운영자 principal ID, 요청 ID, 멱등 키, 사유와 결과를 감사 이벤트에 기록한다. token·JWT·Authorization·query hash와 secret은 기록하지 않는다.
- `Origin` 헤더를 Upbit 서버 간 시세 요청에 임의 추가하지 않는다.

## 11. 배포와 운영

CI는 정적 검사, 전체 단위·계약·통합 테스트, 빈 DB와 기존 DB migration E2E, Playwright, Docker 5종 build를 통과해야 한다. `release` 승격은 같은 40자리 SHA의 성공한 `main` CI run, required status check, 직접·force push 금지, prod required reviewer와 release branch 제한을 API로 확인한 경우에만 허용한다. 확인 실패는 이미지 build 전에 배포를 중단한다. 외부 Action은 commit SHA로 고정하고 workflow는 최소 권한을 사용한다.

스키마·데이터 변경 전에 PostgreSQL volume과 장애 영역이 분리된 위치에 시점 백업을 만들고 원본 DB·SHA, 완료 시각, 크기, checksum, 보관 기한, 복원 명령을 증적으로 남긴다. 최근 복원 rehearsal이 승인된 RPO·RTO를 만족하지 못하면 migration을 실행하지 않는다.

배포 health gate는 API, Web, Gateway, PostgreSQL, 배포된 모든 장기 worker의 container와 DB heartbeat, 원천·집계 row delta 또는 정당한 no-data 사유, freshness, WebSocket 송수신, DB 용량, 오류 log, 서버별 이미지 SHA를 검사한다. Gateway readiness는 필수 env, key file 존재·소유자·권한, egress allowlist를 비밀값 노출 없이 확인한다. 대상 환경은 기존 prod-home과 Tailscale 인프라 `100.107.98.22`이며 운영 서버에서 수동 build·source 수정을 금지한다.

DB migration 성공 뒤 app·web 배포가 실패해도 down migration을 자동 실행하지 않는다. 이전 이미지는 새 schema와 역호환되고 동일 SHA의 전체 이미지 set이 존재한다고 사전 검증한 경우만 사용한다. 그 외에는 승인된 forward-fix SHA로 복구하고 서버별 SHA 불일치가 해소될 때까지 배포를 실패 상태로 유지한다. 각 migration은 이전 app 호환 구간, forward recovery 절차와 소유자를 가진다.

## 12. 성능 확장 결정 gate

다음 도입은 측정과 ADR 없이는 허용하지 않는다.

- 메시지 브로커: PostgreSQL queue의 지연·처리량·잠금이 목표를 지속 위반할 때
- 시계열 DB: PostgreSQL 파티션·인덱스·집계로 목표 범위 조회를 충족하지 못할 때
- Parquet·객체 저장소: 원천 증가량, 보존 비용, 복구 시간 목표가 계층화를 요구할 때
- 워커 분산: 단일 역할 프로세스의 처리량 또는 장애 격리 목표가 측정으로 위반될 때

## 13. 모듈 문서와 계약 색인

- [수집 파이프라인](02_Architecture/upbit-collection-pipeline.md)
- [Upbit API Gateway](02_Architecture/upbit-api-gateway.md)
- [시스템 트레이딩 도메인 목표 설계](02_Architecture/system-trading-domain.md)
- [계약 색인](contracts/README.md)
- [DB 계약](contracts/db/README.md)
- [HTTP·WebSocket 계약](contracts/api/README.md)
- [운영](03_Operations.md)

P1에서 상위 100·활성 50·KST 내부 저장·수동 시작 전제는 모든 KRW 자동 정책, UTC 내부 시간, 내구성 자동 백필과 동적 실시간 구독으로 대체됐다. 기존 테이블과 화면은 무손실 전환을 위한 호환 경로일 뿐 권위 모델이 아니다. 실제 주문 전면 차단은 Issue #33에서 안전한 주문 어댑터와 기본 비활성 capability로 대체한다. 각 후속 Issue는 migration·OpenAPI·JSON Schema를 먼저 추가하고 자동 검증한다.
