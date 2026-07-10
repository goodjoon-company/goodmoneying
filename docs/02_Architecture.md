# 아키텍처 기준

Status: Draft
Last Updated: 2026-07-10

## 목적

이 문서는 goodmoneying 프로젝트의 현재 시스템 구조와 설계 기준 source of truth다.

## 시스템 개요

goodmoneying은 개인용 투자 데이터 플랫폼이다. M1~M3에서 업비트(Upbit) KRW 마켓 데이터 수집, 저장, 품질 확인, 운영 화면, 관심종목, 코인 상세 기반을 구현했다. 현재는 완료된 데이터 기반을 사용해 투자 후보 탐색과 분석 경험을 만드는 초기 구현 이후 제품화(Post-MVP Productization) 단계다.

현재 런타임은 실시간 수집 워커(Realtime Collection Worker), 백필 수집 워커(Backfill Collection Worker), 운영 서버(Operations Server), 운영 화면, PostgreSQL 저장소로 구성한다. 두 수집 워커는 업비트 API에서 데이터를 가져와 PostgreSQL에 원천 사실을 저장하고, 운영 서버는 API와 저장된 화면용 상태를 제공한다. 운영 화면은 React 기반으로 관심종목과 코인 상세를 사용자 가치 화면으로, 운영 상태와 백필(Backfill) 관리를 데이터 신뢰성을 위한 내부 운영 표면으로 제공한다.

운영 화면은 프론트엔드(Frontend) 계산을 최소화한다. 코인별 수집 계획(Collection Plan), 구간형 진행 상태(Coverage Segment), 결측 구간, 표시용 24시간 거래대금 같은 화면용 뷰 모델(View Model)은 수집 또는 배치 시점에 계산해 저장하고, 운영 서버가 조회 API로 제공한다. 관심목록 순서는 `collection_targets.target_order`에 저장하며 `/v1/collection-targets`, `/v1/market-list`, `/v1/dashboard/summary`가 같은 순서를 사용한다. 관심종목 API는 저장 캔들이 없는 후보에도 캔들 커버리지 산정 기준 시작일을 수집 계획에서 내려준다. 관심종목 가격 정보는 실시간 수집 워커가 업비트 웹소켓(WebSocket)으로 저장한 현재가 스냅샷(Ticker Snapshot)을 운영 서버가 `/v1/market-list/stream` SSE(Server-Sent Events)로 브라우저에 push한다. 운영 화면 상단은 같은 관심종목 뷰 모델을 사용해 관심 코인 개수와 관심 코인 목록 레이어 팝업(layer popup)을 모든 화면에서 제공한다.

## 런타임 구조

| 런타임 | 책임 | 현재 구현 | 결정 게이트 |
|---|---|---|---|
| 실시간 수집 워커(Realtime Collection Worker) | 후보 유니버스(Candidate Universe), 현재가 스냅샷(Ticker Snapshot), 체결 이벤트(Trade Event), 호가 요약(Orderbook Summary), 1분 원천 캔들(Source Candle) 실시간 스트림 수집, 수집 품질과 heartbeat 기록 | Python 단일 프로세스, 런타임은 `GOODMONEYING_LIVE_UPBIT=1` live 프로필의 업비트 웹소켓(WebSocket) 스트림만 허용 | 처리량·rate limit·복구 목표를 충족하지 못할 때 다중 워커와 메시지 큐(Message Queue) 검토 |
| 백필 수집 워커(Backfill Collection Worker) | pending 백필 작업(Backfill Job)을 DB 상태 폴링(Polling)으로 확인하고 원천 캔들 백필 수행, fetch 성공 heartbeat와 DB batch upsert 완료 기준 진행 상태 기록 | Python 단일 프로세스, 기본 10초 폴링, 기본 최대 3000개 저장 배치(batch), 동시성(Concurrency) 1 | 백필 처리량이 목표를 충족하지 못할 때 코인별 병렬 백필과 분산 rate limiter 검토 |
| 운영 서버(Operations Server) | 화면 단위 API, 원천 리소스 API, 저장된 뷰 모델 조회, 설정 변경, 백필 제어, 감사 로그(Audit Log) 기록 | FastAPI 단일 인스턴스 | 용량 또는 복구 목표를 충족하지 못할 때 무상태(Stateless) 다중 인스턴스와 고가용성 검토 |
| 운영 화면 | 관심종목, 공통 관심 코인 요약, 코인 상세 레이어, 데이터 수집관리 내비게이션, 운영 상태 대시보드, Backfill 관리 | React, 운영 상태와 관심종목 가격은 SSE 기반 갱신 + React Query HTTP 폴링(Polling) 보조 | 승인된 사용자 시나리오에 패널별 증분 이벤트나 누락 이벤트 복구가 필요할 때 확장 |
| PostgreSQL | 원천 사실, 설정, 품질, 백필, 감사, 알림 이벤트(Notification Event) 저장 | 단일 인스턴스 | 저장량·조회·백업·복구 임계값을 넘을 때 파티셔닝(Partitioning), 복제(Replication), 장애 조치(Failover) 검토 |

## 목표 모듈 지도

현재 상세 구현 모듈은 업비트 수집 파이프라인(Upbit Collection Pipeline)이다. 후속 모듈은 확정된 일정이 아니라 제품 결정 게이트를 통과해야 하는 후보이며, 승인된 시점에 모듈 경계와 계약을 정의한다.

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
| 내부 메시지(Internal Message) | `docs/contracts/protobuf/` | 현재 메시지 계약 없음. 확장성 결정 게이트에서 메시지 큐를 승인하면 코드보다 먼저 스키마(schema)를 기록 |

## 데이터 흐름

### 현재 실시간 수집 흐름

1. 실시간 수집 워커가 DB 설정 테이블에서 후보 유니버스(Candidate Universe), 관심목록으로 표현되는 활성 수집 대상(Active Collection Target), 수집 범위 설정을 읽는다.
2. 실시간 수집 워커는 `GOODMONEYING_LIVE_UPBIT=1` live 프로필에서 업비트 웹소켓(WebSocket)으로 현재가 스냅샷(Ticker Snapshot), 체결 이벤트(Trade Event), 원천 캔들(Source Candle), 호가 요약(Orderbook Summary)을 구독한다. fixture 데이터는 테스트에서 클라이언트를 직접 주입할 때만 사용하며 런타임 후보 유니버스에 저장하지 않는다.
3. 실시간 수집 워커는 수집 실행(Collection Run)과 대상별 수집 결과(Target Collection Result)를 기록한다.
4. 원천 캔들은 `(instrument_id, source, candle_unit, candle_start_at)` 유니크 키로 upsert한다.
5. 현재가 스냅샷과 호가 요약은 `(instrument_id, source, bucket_at)` 유니크 키로 upsert한다. 같은 버킷은 더 늦은 `collected_at`을 가진 성공 수집 결과가 대표 행을 갱신한다.
6. 체결 이벤트는 `(instrument_id, source, sequential_id)` 유니크 키로 중복 저장을 막고, 최근 24시간 시간 버킷별 분당 평균 체결 빈도, 체결강도, 체결량, 체결금액을 운영 대시보드 히트맵으로 노출한다.
7. 데이터 완전성 검사 작업은 목표 범위와 저장 데이터를 비교해 결측 구간(Missing Range)을 생성하거나 해결한다.
8. 실시간 수집 워커 또는 배치 작업은 코인별 수집 계획, 데이터별 최신성, 결측 구간, 구간형 진행 상태를 계산해 저장된 View Model을 갱신한다.
9. 운영 서버는 저장된 View Model을 읽어 운영 대시보드의 정상/주의/장애 상태, 수집 진행률, 화면용 응답을 제공한다. 운영 서버는 조회 요청 중 장시간 계산을 수행하지 않는다.

### 현재 백필 흐름

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
12. 삭제 후 재수집(Destructive Rebuild)은 현재 제품화 범위 밖이며 감사·복구 필요성이 승인될 때 별도 결정한다.

## 아키텍처 결정 게이트

| 제품 단계 또는 조건 | 현재 기준 | 반드시 다시 물어볼 질문 |
|---|---|---|
| 완료 기준선 | PostgreSQL 단일 저장소, 실시간·Backfill 워커 역할 분리, 서버 수집 WebSocket, 브라우저 갱신 SSE + HTTP 폴링 보조 | 현재 계약과 운영 지표에 드리프트가 있는가 |
| P1·P2 제품화 | 관심종목과 코인 상세의 데이터 신뢰·비교 흐름을 우선 | 사용자가 데이터 범위와 최신성을 이해하고 후보를 좁히는 데 부족한 뷰 모델이 무엇인가 |
| P3 전략 실험 준비 | 호가 원천 저장과 기술적 분석 지표는 보류 | 호가 요약만으로 답할 수 없는 전략 시나리오가 있는가, 필요한 지표 계산·캐싱·재현 계약은 무엇인가 |
| 시장 확장 승인 | 국내·미국 주식과 문서·LLM 모듈은 후보 | 새 시장의 거래 시간, 데이터 공급원, 공통 거래 상품 모델, 비용을 어떻게 분리할 것인가 |
| 확장성 임계값 초과 | 현재 단일 인스턴스와 DB 상태 기반 작업 제어를 유지 | 어떤 처리량·복구·저장 임계값을 넘었는가, 메시지 큐·분산 제어·복제 중 무엇이 실제 원인을 해결하는가 |
| 사용자 시나리오 승인 | 외부 알림과 실시간 전송 확장은 후보 | 채널·등급·빈도 제한·재연결·누락 이벤트 복구가 실제 사용자 흐름에 필요한가 |

## 운영과 검증 기준

- 검증 증적은 `docs/Test/`에 실제 명령과 결과로 남긴다.
- 인계가 필요한 변경은 `docs/History/`에 변경 요약, 리스크, 후속 작업을 남긴다.
- 완료된 M1~M3 기준선은 제품 세로 절편과 DB 계약 테스트, 수집 통합 테스트, API 테스트, 브라우저 종단 간 테스트(E2E Test) 증적을 유지한다.
- 기본 자동화 테스트는 mock 또는 명시적으로 주입한 fixture 기반으로 실행하고, 런타임과 E2E(End-to-End)는 fixture 후보 저장 경로를 사용하지 않는다. 실제 업비트 API 부분 호출 검증은 별도 `live` 테스트 프로필(profile)로 분리한다.

현재 미래 결정 게이트는 `docs/ADR/ADR-0007-Post-MVP-아키텍처-결정-게이트.md`를 따른다.

## 변경 규칙

- 모듈 경계, 데이터 흐름, 인프라 구조가 바뀌면 이 문서를 갱신한다.
- DB/API/message의 정확한 schema는 이 문서에 복사하지 않고 `docs/contracts/`에 둔다.
- 되돌리기 어렵거나 여러 영역에 영향이 있는 선택은 `docs/ADR/`에 별도 기록한다.
