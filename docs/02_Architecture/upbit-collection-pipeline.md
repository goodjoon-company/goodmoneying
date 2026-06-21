# 업비트 수집 파이프라인 설계

Status: Draft
Last Updated: 2026-06-21
Related Product: `docs/01_Product.md`
Related Task: `docs/Task/M1-T01-2026-06-17-업비트-수집-운영-mvp-아키텍처-계약-설계.md`
Related DB Contract: `docs/contracts/db/schema.sql`
Related API Contract: `docs/contracts/api/openapi.yaml`

## 책임

업비트 수집 파이프라인(Upbit Collection Pipeline)은 M1의 핵심 경계다. 업비트(Upbit) KRW 마켓 데이터를 수집, 저장, 품질 확인, 운영 상태 노출까지 책임진다.

- 후보 유니버스(Candidate Universe) 갱신
- 활성 수집 대상(Active Collection Target) 최대 50개 유지
- 코인별 수집 계획(Collection Plan)과 저장된 화면용 수집 상태 View Model 유지
- 원천 캔들(Source Candle), 현재가 스냅샷(Ticker Snapshot), 호가 요약(Orderbook Summary) 수집
- 백필(Backfill), 증분 수집(Incremental Collection), 데이터 완전성 검사(Data Completeness Check)
- 수집 실행(Collection Run), 대상별 수집 결과(Target Collection Result), 결측 구간(Missing Range), 수집 진행률(Collection Coverage) 기록
- 수집 워커 heartbeat와 worker 현황판용 상태 View Model 제공
- 운영 서버(Operations Server)를 통한 화면 API와 원천 리소스 API 제공
- 감사 로그(Audit Log), 알림 이벤트(Notification Event) 저장

## 책임이 아닌 것

- 국내 주식과 미국 주식 수집
- 뉴스, 공시, 증권사 리포트 수집
- 대규모 언어 모델(LLM, Large Language Model) 요약과 구조화 신호(Signal)
- 전략, 백테스트(Backtest), 봇(Bot), 시뮬레이션(Simulation), 모의매매(Paper Trading), 실거래(Live Trading)
- 메시지 큐(Message Queue) 기반 다중 워커 작업 분배
- 삭제 후 재수집(Destructive Rebuild)
- 외부 알림 발송

## 구성요소

| 구성요소 | 책임 | 구현 기준 |
|---|---|---|
| 실시간 수집 워커(Realtime Collection Worker) | 업비트 API 호출, rate limit 관리, 후보 유니버스와 증분 수집 실행 | Python 단일 프로세스 |
| 백필 수집 워커(Backfill Collection Worker) | DB 상태 폴링으로 pending 백필 작업 확인, 원천 캔들 결측 구간 백필 실행, fetch 성공 heartbeat와 DB batch upsert 완료 기준 진행 상태 기록 | Python 단일 프로세스, 기본 10초 폴링, 기본 최대 3000개 저장 배치(batch) |
| 운영 서버(Operations Server) | 화면 단위 View Model API, 원천 리소스 API, 쓰기 API, 저장된 worker 상태 조회 | FastAPI |
| 운영 화면 | 데이터 수집관리 내비게이션, worker 현황판, 대시보드, Backfill 관리, 백필 제어, 시장 리스트, 코인 상세 레이어 | React, React Query, HTTP 폴링 |
| PostgreSQL | 원천 사실, 설정, 품질, 감사, 알림 이벤트 저장 | `docs/contracts/db/schema.sql` |

## 입력과 출력

### 입력

- 업비트 KRW 마켓 현재가 API 응답
- 업비트 1분 캔들 API 응답
- 업비트 일봉 API 응답
- 업비트 호가 API 응답
- 운영 화면의 활성 수집 대상 변경
- 운영 화면의 코인별 수집 계획 변경
- 운영 화면의 수집 범위 설정 변경
- 운영 화면의 백필 시작과 제어 명령

### 출력

- PostgreSQL 원천 사실 테이블
- PostgreSQL 코인별 수집 계획과 커버리지(Coverage) View Model 테이블
- 화면 단위 API 응답
- 내부 안정 계약(Internal Stable Contract)인 원천 리소스 API 응답
- 감사 로그(Audit Log)
- 알림 이벤트(Notification Event)

## 주요 흐름

### 후보 유니버스 갱신

1. 수집 워커가 업비트 KRW 마켓 전체 현재가 스냅샷을 조회한다.
2. 24시간 누적 거래대금 기준으로 내림차순 정렬한다.
3. 상위 100개를 후보 유니버스 스냅샷으로 저장한다.
4. 최초 실행 시 상위 50개를 활성 수집 대상으로 자동 체크할 수 있다.
5. 기존 활성 수집 대상이 상위 100 밖으로 이탈해도 자동 제거하지 않고 후보 유니버스 이탈 상태로 표시한다.

### 증분 수집

1. 실시간 수집 워커가 활성 수집 대상을 읽는다.
2. 현재가 스냅샷과 호가 요약은 대상 전체가 1~3분 안에 갱신되도록 1분 주기 목표로 수집한다.
3. 1분 원천 캔들은 매분 직전 완성 캔들을 수집한다.
4. 일봉은 10~30분 주기 또는 하루 마감 후 보정한다.
5. 모든 API 호출은 워커 내부 rate limiter를 통과한다. M1은 두 수집 워커 프로세스가 있으므로 백필 수집 워커 동시성은 1로 제한한다.
6. 각 수집은 수집 실행과 대상별 수집 결과를 남긴다.
7. 실시간 수집 워커는 실행 시작과 성공/오류 상태를 `collection_worker_heartbeats`에 남긴다.
8. 수집 또는 배치 시점에 코인별 수집 계획의 기간, 데이터별 최신성, 결측 구간, 구간형 진행 상태를 계산해 저장된 View Model을 갱신한다.

### 백필

1. 사용자는 Backfill 관리 화면에서 백필 후보 코인을 체크한다.
2. 사용자가 백필 계획 생성 버튼을 누르면 운영 화면은 수집 범위와 백필 옵션을 설정하는 레이어 팝업을 연다.
3. 운영 서버는 선택 코인 세트, 데이터 유형, 목표 기간으로 백필 계획을 생성한다.
4. 백필 계획은 대상, 기간, 예상 요청 수, 저장 예상량을 보수적 추정치로 보여준다.
5. 사용자가 백필 시작 버튼을 누르면 계획별 백필 작업이 pending 상태로 저장된다.
6. 운영 화면은 저장된 백필 작업을 백필 작업 패널에 목록으로 구성하고, 멈춤(Pause), 재개(Resume), 중지(Stop), 삭제(Delete) 제어를 제공한다. 일시정지 또는 실패 상태의 작업에는 재개 버튼을 제공한다.
7. 백필 수집 워커는 DB 폴링으로 작업 상태를 10초 주기로 읽고 백필을 실행한다.
8. 백필 수집 워커는 폴링 heartbeat와 성공/오류 상태를 `collection_worker_heartbeats`에 남긴다.
   장시간 백필 작업 중에는 업비트 fetch 성공 지점마다 heartbeat를 갱신해 실행 중인 worker가 지연으로 오판되지 않게 한다.
9. 백필 수집 워커는 목표 범위와 저장된 캔들 시작 시각을 비교해 이미 저장된 분(minute)을 업비트에 다시 요청하지 않고 없는 결측 구간만 요청한다.
10. 업비트 fetch page는 200개 단위를 유지하고, DB 저장은 기본 최대 3000개 batch 단위로 upsert한다. batch 크기는 `GOODMONEYING_BACKFILL_BATCH_SIZE` 외부 설정으로 바꿀 수 있다.
11. `rows_written_count`와 `last_completed_at`은 DB batch upsert가 성공한 뒤에만 갱신한다.
12. 기간이 조정된 경우 수집 범위 시작일부터 재검사하되, 시작일 데이터가 이미 있으면 그 이후 첫 빈 구간부터 요청한다.
13. 백필은 일시정지, 중지, 이어서하기, 안전 재시작을 지원한다. 실패 상태에서 이어서하기를 수행하면 기존 저장 데이터를 삭제하지 않고 결측 구간을 다시 계산해 없는 구간만 요청한다.
14. 삭제 후 재수집은 M1 이후 기능이다.

### 데이터 완전성 검사

1. 데이터 완전성 검사 작업은 목표 수집 범위와 저장 데이터를 비교한다.
2. 기대 데이터가 없거나 복구가 필요한 구간은 결측 구간으로 저장한다.
3. 백필로 복구된 결측 구간은 해결 상태로 전환한다.
4. 운영 서버는 결측 구간과 최신성을 읽어 수집 진행률과 화면용 상태를 계산한다.

## 데이터 기준

- 저장 시각(Storage Time)은 KST(Korea Standard Time) 기준 `timestamptz`다.
- 업비트 KRW 마켓 표시 시각(Display Time)은 KST(Korea Standard Time)를 기본으로 한다.
- 금액, 수량, 거래대금, 등락률은 DB에서 `numeric`, Python에서 `Decimal`로 다룬다.
- API 응답의 Decimal 값은 문자열로 보낸다.
- 원천 캔들 유니크 키는 `(instrument_id, source, candle_unit, candle_start_at)`이다.
- 현재가 스냅샷과 호가 요약 유니크 키는 `(instrument_id, source, bucket_at)`이다.
- 같은 수집 버킷 시간(Collection Bucket Time)에 재수집이 발생하면 더 늦은 `collected_at`을 가진 성공 수집 결과가 대표 행을 갱신한다.
- 백필 수집의 `rows_written_count`와 `last_completed_at`은 fetch 성공이 아니라 DB batch upsert 성공을 기준으로 한다.
- 백필 저장 배치(batch)는 기본 최대 3000개 row이며, 운영 환경에서는 `GOODMONEYING_BACKFILL_BATCH_SIZE`로 조정한다.
- 워커 로그 레벨은 `GOODMONEYING_LOG_LEVEL`로 조정한다. 기본값은 `INFO`이며, 운영 장애 분석 시 `DEBUG`로 올려 백필 job, target, 결측 범위, fetch, DB batch upsert 경계를 확인한다.

## 운영 화면

| 화면 | API 성격 | 자동 갱신 |
|---|---|---|
| 데이터 수집관리 내비게이션 | 제품 전체 메뉴와 MVP 활성 영역 | 정적 또는 설정 변경 후 갱신 |
| 운영 상태 대시보드 | worker 현황판, 코인별 수집 계획, 파이프라인 건강도, 최신성, 실패, 결측, 저장량, 구간형 진행 상태 | 10~15초 |
| Backfill 관리 | 후보 유니버스, 활성 수집 대상 최대 50개, 24시간 거래대금, 수집 시작일/최종일, 실시간 수집 라벨, 백필 계획 생성 레이어, 백필 작업 패널 | 수동 또는 변경 후 갱신 |
| 백필 작업 | 저장된 백필 작업 상태와 제어 | 실행 중 5~10초 |
| 시장 리스트 | 현재가, 거래대금, 등락률, 품질 상태 | 30초 |
| 코인 상세 레이어 | 캔들 차트, 호가 요약, 품질 이력 | 30초 또는 사용자가 켜는 실시간 모드 |

운영 상태 대시보드는 수집 대상 코인을 행(row) 단위로 표시한다. 각 행은 코인 전체 상태와 캔들(Candle), 현재가(Ticker), 호가 요약(Orderbook Summary)의 미니 상태를 함께 보여주고, 펼치면 데이터별 그래프, 결측 구간, 수집 계획 수정 버튼, 백필 제어를 표시한다.

운영 상태 대시보드 첫 카드의 worker 현황판은 `DashboardSummary.workerStatus`를 사용한다. 실시간 수집 워커는 heartbeat, 마지막 저장 성공 시각, 24시간 수집 오류 수, 24시간 실패율, 최근 오류 상세를 표시한다. 백필 수집 워커는 heartbeat, 마지막 저장 성공 시각, 전체 백필 오류 수, 전체 실패율, 현재 실행 중인 단일 백필 계획 기준의 동작 중 대상 수(`runningTargetCount/totalTargetCount`), 대기 중인 백필 job/target 보조지표(`queuedJobCount/queuedTargetCount`), 최근 오류 상세를 표시한다. worker 상태 라벨은 클릭 가능한 진단 진입점이며, 상태 사유, 마지막 heartbeat, 마지막 저장 성공, 오류율, 동작 중 대상 수, 대기 백필 수 같은 `diagnostics` 항목을 레이어 팝업으로 표시한다.

화면 시간 표시는 KST(Korea Standard Time)로 통일한다. 저장과 내부 계산, Docker 컨테이너, PostgreSQL 세션과 DB 기본 시간대도 KST 기준이고, 현재(지속) 수집의 진행 상태 기준일은 KST 전일 23:59:59다.

## 보안과 감사

- M1은 로컬 신뢰 네트워크를 전제로 한다.
- 쓰기 API는 단순 운영 토큰(Authentication)을 요구한다.
- 활성 수집 대상 저장, 수집 범위 설정 변경, 백필 시작/제어는 감사 로그를 남긴다.
- 다중 사용자 권한(Authorization)은 M1 범위가 아니다.

## 의존성

- 업비트 API
- PostgreSQL
- FastAPI
- React
- Docker Compose

## 관련 계약

- DB: `docs/contracts/db/schema.sql`
- API: `docs/contracts/api/openapi.yaml`

## 리스크와 후속 작업

- M1은 단일 워커 구조이므로 다중 워커 장애 복구와 작업 분배는 M3.5에서 고도화한다.
- M1은 삭제 없음 정책을 사용하므로 저장량 증가를 M3/M3.5에서 반드시 재검토한다.
- 메시지 큐(Message Queue), 분산 rate limiter, PostgreSQL 복제/장애 조치(Failover)는 M3.5 필수 결정 항목이다.
- 기술적 분석 지표, 외부 알림 발송, SSE/WebSocket은 MVP 이후 별도 결정 질문을 거쳐 설계한다.
