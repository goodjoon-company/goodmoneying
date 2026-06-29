# 아키텍처 기준

Status: Draft
Last Updated: 2026-06-30

## 목적

이 문서는 goodmoneying 프로젝트의 현재 시스템 구조와 설계 기준 source of truth다.

## 시스템 개요

goodmoneying은 개인용 투자 데이터 플랫폼이다. 현재 M1 범위는 업비트(Upbit) KRW 마켓 데이터 수집, 저장, 품질 확인, 운영 화면 제공에 집중한다. 후속 단계에서는 국내 주식, 미국 주식, 뉴스/공시/리포트, 대규모 언어 모델(LLM, Large Language Model) 신호, 전략, 봇(Bot), 시뮬레이션(Simulation), 모의매매(Paper Trading), 실거래(Live Trading)가 같은 아키텍처 골격 위에 붙는다.

M1은 실시간 수집 워커(Realtime Collection Worker), 백필 수집 워커(Backfill Collection Worker), 운영 서버(Operations Server), 운영 화면, PostgreSQL 저장소로 구성한다. 두 수집 워커는 업비트 API에서 데이터를 가져와 PostgreSQL에 원천 사실을 저장하고, 운영 서버는 API와 화면용 상태 계산을 제공한다. 운영 화면은 React 기반으로 최상단 1레벨 관심종목 메뉴, 데이터 수집관리 좌측 내비게이션(Navigation), 운영 상태, 수집 대상, 코인 상세, 백필(Backfill) 상태를 보여준다.

M2부터 운영 화면은 프론트엔드(Frontend) 계산을 최소화한다. 코인별 수집 계획(Collection Plan), 구간형 진행 상태(Coverage Segment), 결측 구간, 표시용 24시간 거래대금 같은 화면용 View Model은 수집 또는 배치 시점에 계산해 저장하고, 운영 서버가 조회 API로 제공한다. 관심목록 순서는 `collection_targets.target_order`에 저장하며 `/v1/collection-targets`, `/v1/market-list`, `/v1/dashboard/summary`가 같은 순서를 사용한다. 관심종목 API는 저장 캔들이 없는 후보에도 캔들 커버리지 산정 기준 시작일을 수집 계획에서 내려준다. 관심종목 가격 정보는 실시간 수집 워커가 업비트 웹소켓(WebSocket)으로 저장한 현재가 스냅샷(Ticker Snapshot)을 운영 서버가 `/v1/market-list/stream` SSE(Server-Sent Events)로 브라우저에 push한다. 운영 화면 상단은 같은 관심종목 View Model을 사용해 관심 코인 개수와 관심 코인 목록 레이어 팝업(layer popup)을 모든 화면에서 제공한다.

## 런타임 구조

| 런타임 | 책임 | M1 구현 | 확장 방향 |
|---|---|---|---|
| 실시간 수집 워커(Realtime Collection Worker) | 후보 유니버스(Candidate Universe), 현재가 스냅샷(Ticker Snapshot), 체결 이벤트(Trade Event), 호가 요약(Orderbook Summary), 1분 원천 캔들(Source Candle) 실시간 스트림 수집, 수집 품질과 heartbeat 기록 | Python 단일 프로세스, live 프로필은 업비트 웹소켓(WebSocket) 스트림, fixture 프로필은 REST 기반 1회 수집 | M4에서 다중 워커와 메시지 큐(Message Queue) 기반 작업 분배 검토 |
| 백필 수집 워커(Backfill Collection Worker) | pending 백필 작업(Backfill Job)을 DB 상태 폴링(Polling)으로 확인하고 원천 캔들 백필 수행, fetch 성공 heartbeat와 DB batch upsert 완료 기준 진행 상태 기록 | Python 단일 프로세스, 기본 10초 폴링, 기본 최대 3000개 저장 배치(batch), 동시성(Concurrency) 1 | M4에서 코인별 병렬 백필, 분산 rate limiter, 메시지 큐 검토 |
| 운영 서버(Operations Server) | 화면 단위 API, 원천 리소스 API, 저장된 View Model 조회, 설정 변경, 백필 제어, 감사 로그(Audit Log) 기록 | FastAPI | M4에서 stateless 다중 인스턴스와 고가용성(High Availability) 검토 |
| 운영 화면 | 최상단 1레벨 관심종목 메뉴, 공통 관심 코인 요약, 데이터 수집관리 내비게이션, 운영 상태 대시보드, Backfill 관리, 코인 상세 레이어, 백필 작업 화면 | React, 운영 상태와 관심종목 가격은 SSE(Server-Sent Events) 기반 갱신 + React Query HTTP 폴링(Polling) 보조 | 후속 고빈도 화면에서 패널별 증분 이벤트와 재연결 복구 정책 확장 |
| PostgreSQL | 원천 사실, 설정, 품질, 백필, 감사, 알림 이벤트(Notification Event) 저장 | 단일 인스턴스 | M3/M4에서 파티셔닝(Partitioning), 백업/복구, 복제(Replication), 장애 조치(Failover) 검토 |

## 목표 모듈 지도

M1에서는 업비트 수집 파이프라인(Upbit Collection Pipeline)만 상세 구현한다. 후속 모듈은 아키텍처 의존 방향과 연결 지점만 선반영하고, 상세 계약은 해당 마일스톤에서 확정한다.

## 모듈 색인

| 모듈 | 설계 문서 | 책임 | 주요 의존성 |
|---|---|---|---|
| 업비트 수집 파이프라인(Upbit Collection Pipeline) | `docs/02_Architecture/upbit-collection-pipeline.md` | 업비트 KRW 마켓 수집, 저장, 품질 확인, 운영 API/화면 제공 | PostgreSQL, 업비트 API, `docs/contracts/db/schema.sql`, `docs/contracts/api/openapi.yaml` |
| 국내 주식 수집 | 후속 작성 | 국내 주식 가격/거래량, 시가총액, 수급, 공매도(Short Selling), 재무지표 수집 | 업비트 수집 파이프라인의 수집 진행률(Collection Coverage), 품질 모델 재사용 |
| 미국 주식 수집 | 후속 작성 | 미국 주식 가격/거래량, 시가총액, 재무지표 수집 | 시장별 거래 시간 정책, 공통 거래 상품(Instrument) 모델 |
| 문서/이벤트 수집 | 후속 작성 | 뉴스, 공시, 증권사 리포트 원천 수집 | 거래 상품, 외부 문서 공급원, 저장소 |
| LLM 신호 | 후속 작성 | 뉴스/공시/리포트 요약과 구조화 신호(Signal) 생성 | 문서/이벤트 수집, 시계열(Time Series) 정렬 |
| 전략과 백테스트(Backtest) | 후속 작성 | 데이터와 신호를 조합한 전략 설계와 과거 검증 | 시장 데이터, LLM 신호, 파생 캔들(Derived Candle) |
| 봇과 시뮬레이션 | 후속 작성 | 전략 파이프라인(Pipeline), 봇 설정, 실제 주문 없는 판단/손익 시뮬레이션 | 전략, 백테스트, 시장 데이터 |

## 계약 위치

| 계약 | 위치 | 기준 |
|---|---|---|
| DB schema | `docs/contracts/db/schema.sql` | PostgreSQL 기준 schema |
| HTTP API | `docs/contracts/api/openapi.yaml` | FastAPI 운영 서버가 제공해야 하는 OpenAPI 계약 |
| Internal message | `docs/contracts/protobuf/` | M1에서는 메시지 계약 없음. M4 메시지 큐 도입 시 이 위치 또는 repo가 선택한 schema 파일에 기록 |

## 데이터 흐름

### M1 수집 흐름

1. 실시간 수집 워커가 DB 설정 테이블에서 후보 유니버스(Candidate Universe), 관심목록으로 표현되는 활성 수집 대상(Active Collection Target), 수집 범위 설정을 읽는다.
2. 실시간 수집 워커가 live 프로필에서는 업비트 웹소켓(WebSocket)으로 현재가 스냅샷(Ticker Snapshot), 체결 이벤트(Trade Event), 원천 캔들(Source Candle), 호가 요약(Orderbook Summary)을 구독하고, fixture 프로필에서는 REST 기반 1회 수집으로 로컬 개발 데이터를 만든다.
3. 실시간 수집 워커는 수집 실행(Collection Run)과 대상별 수집 결과(Target Collection Result)를 기록한다.
4. 원천 캔들은 `(instrument_id, source, candle_unit, candle_start_at)` 유니크 키로 upsert한다.
5. 현재가 스냅샷과 호가 요약은 `(instrument_id, source, bucket_at)` 유니크 키로 upsert한다. 같은 버킷은 더 늦은 `collected_at`을 가진 성공 수집 결과가 대표 행을 갱신한다.
6. 체결 이벤트는 `(instrument_id, source, sequential_id)` 유니크 키로 중복 저장을 막고, 최근 24시간 시간 버킷별 분당 평균 체결 빈도, 체결강도, 체결량, 체결금액을 운영 대시보드 히트맵으로 노출한다.
7. 데이터 완전성 검사 작업은 목표 범위와 저장 데이터를 비교해 결측 구간(Missing Range)을 생성하거나 해결한다.
8. 실시간 수집 워커 또는 배치 작업은 코인별 수집 계획, 데이터별 최신성, 결측 구간, 구간형 진행 상태를 계산해 저장된 View Model을 갱신한다.
9. 운영 서버는 저장된 View Model을 읽어 운영 대시보드의 정상/주의/장애 상태, 수집 진행률, 화면용 응답을 제공한다. 운영 서버는 조회 요청 중 장시간 계산을 수행하지 않는다.

### M1 백필 흐름

1. 사용자는 Backfill 관리 화면에서 백필 후보 코인을 체크한다.
2. 운영 화면은 선택된 코인 세트로 백필 계획 생성 레이어 팝업(layer popup)을 열고, 수집 범위와 안전 재시작(Safe Restart) 옵션을 입력받는다.
3. 사용자가 백필 시작 버튼을 누르면 운영 서버는 선택 코인, 데이터 유형, 목표 기간을 기준으로 백필 작업(Backfill Job)을 `pending` 상태로 저장한다.
4. 운영 화면은 저장된 백필 작업을 백필 작업 패널에 목록으로 표시하고 진행 상태, 대상 코인, 기간, 제어 버튼을 제공한다.
5. 백필 수집 워커는 DB 상태 폴링(Polling)으로 `pending` 백필 작업 상태를 10초 주기로 읽고 저장 순서대로 실행한다.
6. 백필 수집 워커는 작업을 실행할 때 상태를 `running`으로 전환하고, 일시정지(Pause) 또는 중지(Stop)된 작업은 점유하지 않는다.
7. 백필 수집 워커는 목표 범위와 저장된 캔들 시작 시각을 비교해 이미 저장된 분(minute)을 업비트에 다시 요청하지 않고 없는 결측 구간만 요청한다.
8. 업비트 fetch page는 200개 단위를 유지하고, DB 저장은 기본 최대 3000개 batch 단위로 upsert한다. batch 크기는 `GOODMONEYING_BACKFILL_BATCH_SIZE` 외부 설정으로 바꿀 수 있다.
9. fetch가 성공하면 `backfill_collection` heartbeat를 갱신한다. `rows_written`과 `last_completed_at`은 DB batch upsert가 성공한 뒤에만 갱신한다.
10. 사용자는 실행 중인 백필 작업을 일시정지(Pause), 중지(Stop), 이어서하기(Resume), 안전 재시작할 수 있다. 실패(failed)한 백필 작업도 재개할 수 있으며, 재개 시 저장 상태를 다시 계산해 없는 결측 구간만 요청한다.
11. 안전 재시작은 기존 데이터를 삭제하지 않고 목표 범위 전체를 재검사한다.
12. 삭제 후 재수집(Destructive Rebuild)은 M1 이후 기능으로 둔다.

## 아키텍처 로드맵

| 시점 | 결정 또는 고도화 | 반드시 다시 물어볼 질문 |
|---|---|---|
| M1 | PostgreSQL 단일 저장소, 실시간 수집 워커와 백필 수집 워커 역할 분리, live 실시간 수집은 WebSocket, 운영 화면은 SSE + HTTP 폴링 보조 | 구현 중 계약이 제품 요구사항과 충돌하는가 |
| M2 | 데이터 수집관리 화면 View Model | 프론트엔드가 계산하지 않아도 되는 응답 shape가 충분한가, 코인별 수집 계획과 구간형 진행 상태를 저장할 계약이 충분한가 |
| M3 | 호가 원천 스냅샷(Snapshot) 저장 확대 | 보존 기간, 파티셔닝, 압축, 다운샘플링(Downsampling), 별도 저장소가 필요한가 |
| M4 | 수평 확장(Horizontal Scaling)과 고가용성 고도화 | 메시지 큐 기술은 무엇인가, 다중 워커 작업 분배와 제어 이벤트를 어떻게 처리할 것인가, PostgreSQL 복제/장애 조치 전략은 무엇인가 |
| M5 전 | 국내 주식 확장 게이트 | 업비트 데이터 모델을 공통 시장 데이터 모델로 어디까지 일반화할 것인가 |
| MVP 이후 | 외부 알림 발송 | 채널, 등급, 빈도 제한, 확인/해결 상태, 다중 수신자 확장 여부 |
| MVP 이후 | 기술적 분석 지표 | 지표 계산 위치, 캐싱, 전략 입력 연결, 사용자 정의 지표 범위 |
| 후속 실시간 화면 | 실시간 전송 방식 확장 | 대시보드 요약 SSE 이후 패널별 증분 이벤트가 필요한가, 재연결과 누락 이벤트 복구가 필요한가 |

## 운영과 검증 기준

- 검증 증적은 `docs/Test/`에 실제 명령과 결과로 남긴다.
- 인계가 필요한 변경은 `docs/History/`에 변경 요약, 리스크, 후속 작업을 남긴다.
- M1 완료는 제품 세로 절편 기준으로 판단한다. 백엔드 수집만 끝난 상태나 빈 화면만 있는 상태는 완료로 보지 않는다.
- M1 검증은 DB 계약 테스트, 수집 통합 테스트, API 테스트, 브라우저 E2E(End-to-End) 테스트를 포함한다.
- 기본 자동화 테스트는 mock/fixture 기반으로 실행하고, 실제 업비트 API 부분 호출 검증은 별도 `live` 테스트 프로필(profile)로 분리한다.

## 변경 규칙

- 모듈 경계, 데이터 흐름, 인프라 구조가 바뀌면 이 문서를 갱신한다.
- DB/API/message의 정확한 schema는 이 문서에 복사하지 않고 `docs/contracts/`에 둔다.
- 되돌리기 어렵거나 여러 영역에 영향이 있는 선택은 `docs/ADR/`에 별도 기록한다.
