# 계약 개발 사양

`docs/contracts/`는 DB 스키마(schema), HTTP·WebSocket 인터페이스(interface), 내부 메시지 스키마의 단일 기준(source of truth)이다. 구현 코드나 아키텍처 문서보다 이 파일들을 먼저 읽고 변경한다.

## 계약 소비자와 읽는 순서

| 변경하려는 대상 | 먼저 읽을 파일 | 주 소비자 | 자동 검증 |
|---|---|---|---|
| 테이블·인덱스·제약조건 | `db/migrations/*.sql` | dbmate, API, 워커, 저장소 구현 | `tests/contracts/test_db_contract.py`, 마이그레이션 E2E |
| HTTP 경로·요청·응답·오류 | `api/openapi.yaml` | 웹, FastAPI | `tests/contracts/test_api_contract.py` |
| 업비트 게이트웨이 HTTP 경계 | `api/upbit-gateway.openapi.yaml` | 업비트 API 작업대, 게이트웨이 | `tests/contracts/test_upbit_gateway_contract.py` |
| 업비트 게이트웨이 WebSocket 이벤트 | `api/upbit-gateway-websocket.schema.json` | 업비트 API 작업대, 게이트웨이 | `tests/contracts/test_upbit_gateway_contract.py` |
| 공식 Upbit 기능·파라미터·안전 정책 | `upbit/upbit-api-catalog.yaml` | 업비트 API 작업대, 게이트웨이 | `tests/contracts/test_upbit_gateway_contract.py` |
| Upbit myOrder 내부 대사 입력 | `upbit/myorder-event.md` | P6 private 주문 대사 parser | `tests/contracts/test_p6_myorder_contract.py` |
| Upbit REST 주문 snapshot 대사 | `upbit/rest-order-reconciliation.md` | P6 REST snapshot 대사 adapter | `tests/contracts/test_p6_rest_reconciliation_contract.py` |
| 백테스트 엔진 결정론·체결 가정 | `backtest-engine.md` | 공유 도메인 엔진, Backtest Worker, Backtest Lab | `tests/contracts/test_p4_backtest_contract.py` |
| 코인 분석 실시간 메시지 | `api/realtime-analysis-websocket.schema.json` | 분석 화면, 운영 서버 | API·프론트엔드 메시지 테스트 |
| 시스템 관리 실시간 메시지 | `api/realtime-system-management-websocket.md` | 시스템 관리 화면, 운영 서버 | API·프론트엔드 메시지 테스트 |
| 미래 내부 이벤트 | `protobuf/` | 승인된 메시지 큐 소비자 | 스키마별 테스트 |

P1 데이터 기반 계약은 `db/migrations/20260717000100_system_trading_data_foundation.sql`과 `api/openapi.yaml`의 `/v1/data-foundation` 경로로 구현됐다. P2-3 버전 지표·시장 통계는 `db/migrations/20260717001100_p2_versioned_indicators.sql`, OpenAPI의 `/v1/instruments/{instrumentId}/indicators`·`/market-statistics`, `api/realtime-analysis-websocket.schema.json`을 함께 단일 기준으로 사용한다. 현재 별도 메시지 브로커 계약은 없다. 내부 실시간 envelope의 목표 의미는 [도메인 설계](../02_Architecture/system-trading-domain.md)를 따르며 Issue #29에서 JSON Schema를 이 디렉터리에 추가한 시점부터 그 파일이 기계 계약의 단일 기준이다. `protobuf/`는 브로커 도입 결정 이후에만 기계 검증 스키마를 추가하는 예약 경계다.

## 구조

| 영역 | 위치 | 예시 |
|---|---|---|
| DB | `docs/contracts/db/` | 변경 이력 `migrations/*.sql`, 생성 스냅샷 `schema.sql` |
| API | `docs/contracts/api/` | `openapi.yaml` |
| Upbit | `docs/contracts/upbit/` | `upbit-api-catalog.yaml` |
| Backtest | `docs/contracts/backtest-engine.md` | 순수 엔진 계약과 후속 Worker/API 확장 경계 |
| Message | `docs/contracts/protobuf/` | `*.proto` |

## 변경 순서

1. 호환성(Compatibility), 인증(Authentication), 데이터 이전(Migration) 영향과 함께 계약 파일을 먼저 수정한다.
2. `docs/02_Architecture.md`와 관련 모듈 설계 문서에 **경계와 영향만** 반영한다. 필드 표를 복제하지 않는다.
3. 코드 DTO(Data Transfer Object), SQL, handler, serializer, 프론트엔드 소비자를 수정한다.
4. 계약 테스트와 영향받는 통합·종단 간(E2E, End-to-End) 테스트를 실행하고 `docs/Test/`에 실제 결과를 기록한다.
5. 호환성이 깨지거나 되돌리기 어려운 선택이면 ADR을 만들고, `docs/History/`에 drift 위험과 후속 작업을 남긴다.

## 규칙

- 기계가 검증할 수 있는 정의는 표가 아니라 계약 파일로 관리한다.
- Architecture 문서는 계약 내용을 복붙하지 않고 위치와 운영 원칙만 링크한다.
- 코드와 계약이 다르면 계약을 기준으로 정합성 Task를 만든다.
- 계약 문서의 설명과 기계 검증 파일이 충돌하면 기계 검증 파일을 우선하고 설명을 즉시 정정한다.
