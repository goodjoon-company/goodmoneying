# 아키텍처 기준

Status: Draft
Last Updated: 2026-06-16

## 목적

이 문서는 goodmoneying 프로젝트의 현재 시스템 구조와 설계 기준 source of truth다.

## 시스템 개요

TBD: 런타임, 주요 구성요소, 외부 의존성, 데이터 저장소를 코드가 생긴 뒤 실제 구현 기준으로 기록한다.

## 모듈 색인

| 모듈 | 설계 문서 | 책임 | 주요 의존성 |
|---|---|---|---|
| TBD | `docs/02_Architecture/TBD.md` | TBD | TBD |

## 계약 위치

| 계약 | 위치 | 기준 |
|---|---|---|
| DB schema | `docs/contracts/db/` | SQL schema 또는 migration |
| HTTP API | `docs/contracts/api/` | OpenAPI 또는 repo가 선택한 API schema |
| Internal message | `docs/contracts/protobuf/` | Protobuf 또는 repo가 선택한 message schema |

## 데이터 흐름

TBD: 사용자 요청, 내부 처리, 저장, 외부 시스템 연동 흐름을 Mermaid 또는 단계형 설명으로 기록한다.

## 운영과 검증 기준

- 검증 증적은 `docs/Test/`에 실제 명령과 결과로 남긴다.
- 인계가 필요한 변경은 `docs/History/`에 변경 요약, 리스크, 후속 작업을 남긴다.

## 변경 규칙

- 모듈 경계, 데이터 흐름, 인프라 구조가 바뀌면 이 문서를 갱신한다.
- DB/API/message의 정확한 schema는 이 문서에 복사하지 않고 `docs/contracts/`에 둔다.
- 되돌리기 어렵거나 여러 영역에 영향이 있는 선택은 `docs/ADR/`에 별도 기록한다.
