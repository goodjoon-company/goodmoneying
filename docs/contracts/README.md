# 계약 기준

`docs/contracts/`는 DB schema, API interface, internal message schema의 source of truth다.

## 구조

| 영역 | 위치 | 예시 |
|---|---|---|
| DB | `docs/contracts/db/` | `schema.sql`, migration reference |
| API | `docs/contracts/api/` | `openapi.yaml` |
| Message | `docs/contracts/protobuf/` | `*.proto` |

## 변경 순서

1. 계약 파일을 먼저 수정한다.
2. `docs/02_Architecture.md`와 관련 모듈 설계 문서에 링크와 요약을 반영한다.
3. 코드 DTO, SQL, handler, serializer를 수정한다.
4. `docs/Test/`에 실제 검증 결과를 기록한다.
5. `docs/History/`에 breaking change, drift 위험, 후속 작업을 남긴다.

## 규칙

- 기계가 검증할 수 있는 정의는 표가 아니라 계약 파일로 관리한다.
- Architecture 문서는 계약 내용을 복붙하지 않고 위치와 운영 원칙만 링크한다.
- 코드와 계약이 다르면 계약을 기준으로 정합성 Task를 만든다.
