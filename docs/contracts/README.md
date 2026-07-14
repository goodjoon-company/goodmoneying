# 계약 개발 사양

`docs/contracts/`는 DB 스키마(schema), HTTP·WebSocket 인터페이스(interface), 내부 메시지 스키마의 단일 기준(source of truth)이다. 구현 코드나 아키텍처 문서보다 이 파일들을 먼저 읽고 변경한다.

## 계약 소비자와 읽는 순서

| 변경하려는 대상 | 먼저 읽을 파일 | 주 소비자 | 자동 검증 |
|---|---|---|---|
| 테이블·인덱스·제약조건 | `db/schema.sql` | API, 워커, 저장소 구현 | `tests/contracts/test_db_contract.py` |
| HTTP 경로·요청·응답·오류 | `api/openapi.yaml` | 웹, FastAPI | `tests/contracts/test_api_contract.py` |
| 코인 분석 실시간 메시지 | `api/realtime-analysis-websocket.schema.json` | 분석 화면, 운영 서버 | API·프론트엔드 메시지 테스트 |
| 시스템 관리 실시간 메시지 | `api/realtime-system-management-websocket.md` | 시스템 관리 화면, 운영 서버 | API·프론트엔드 메시지 테스트 |
| 미래 내부 이벤트 | `protobuf/` | 승인된 메시지 큐 소비자 | 스키마별 테스트 |

현재 내부 메시지 큐 계약은 없다. `protobuf/`는 메시지 큐 도입 결정 이후에만 기계 검증 스키마를 추가하는 예약 경계다.

## 구조

| 영역 | 위치 | 예시 |
|---|---|---|
| DB | `docs/contracts/db/` | `schema.sql`, migration reference |
| API | `docs/contracts/api/` | `openapi.yaml` |
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
