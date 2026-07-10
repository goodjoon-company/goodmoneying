# ADR-0002-M1-PostgreSQL-단일-저장소

Status: Accepted
Date: 2026-06-17
미래 결정 게이트 정제(Future Gate Refined By): `docs/ADR/ADR-0007-Post-MVP-아키텍처-결정-게이트.md`

## 맥락

M1은 원천 캔들(Source Candle), 현재가 스냅샷(Ticker Snapshot), 호가 요약(Orderbook Summary), 수집 실행(Collection Run), 대상별 수집 결과(Target Collection Result), 결측 구간(Missing Range), 백필 작업(Backfill Job), 감사 로그(Audit Log), 알림 이벤트(Notification Event)를 저장해야 한다. 초기에는 데이터 의미, 품질, 운영 화면의 정합성이 저장 성능 최적화보다 중요하다.

## 결정

M1 저장소는 PostgreSQL 단일 저장소로 시작한다. DB는 원천 사실 테이블 중심으로 설계하고, 화면용 상태와 진행률 비율은 운영 서버(Operations Server)가 계산한다. M1에서는 데이터 삭제, 다운샘플링(Downsampling), 별도 시계열 DB(Time-series DB), ClickHouse, 객체 저장소(Object Storage)를 도입하지 않는다.

## 대안

| 대안 | 장점 | 단점 |
|---|---|---|
| PostgreSQL 단일 저장소 | SQL 계약 명확성, 유니크 제약(Unique Constraint), upsert, 운영 조회와 품질 집계가 쉬움 | 고빈도 장기 호가 원천 저장으로 확장하면 파티셔닝(Partitioning)이나 별도 저장소가 필요할 수 있음 |
| SQLite | 로컬 시작이 쉬움 | 24시간 수집, 운영 서버 동시 조회, 후속 원천 호가 저장에 한계가 빠름 |
| 시계열 DB 또는 ClickHouse 선도입 | 장기 시계열과 고속 집계에 강함 | M1 운영 복잡도와 계약 관리 부담이 큼 |

## 결과

- 모든 저장 시각(Storage Time)은 절대 시각을 보존하는 `timestamptz`로 둔다. 현재 DB 계약과 런타임은 PostgreSQL 세션·애플리케이션 계산·API 표시를 KST(Korea Standard Time, `Asia/Seoul`)로 통일한다.
- 가격, 수량, 거래대금, 스프레드(Spread), 잔량, 등락률, 호가 불균형(Imbalance)은 DB에서 `numeric`, Python에서 `Decimal`로 다룬다.
- API 응답의 Decimal 값은 문자열로 보낸다.
- M1 데이터 보존 정책은 삭제 없음이다.

## 후속 작업

- 과거 계획은 M3 또는 M3.5에서 보존 기간과 저장 구조를 결정하도록 두었다. 현재는 저장량, 조회 시간, 백업·복구 시간이 운영 임계값을 넘거나 승인된 전략 실험이 필요로 할 때 `ADR-0007`의 결정 게이트를 적용한다.
- 삭제 후 재수집(Destructive Rebuild)은 감사(Audit)와 복구 정책을 갖춘 뒤 후속 구현한다.
