# AGENTS.md

이 저장소는 문서화와 인계(handoff)에 `goodjoon-workflow`를 따른다.

## 문서 단일 기준(Sources Of Truth)

| 영역                              | 위치                        | 규칙                                 |
| ------------------------------- | ------------------------- | ---------------------------------- |
| 제품 범위와 정책                       | `docs/01_Product.md`      | 요구사항, 제품 경계, 로드맵(roadmap), 비기술 정책  |
| 아키텍처 색인(Architecture Index)     | `docs/02_Architecture.md` | 시스템 경계, 모듈 색인, 계약 위치               |
| 모듈 설계(Module Design)            | `docs/02_Architecture/`   | 모듈별 책임, 흐름, 의존성, 리스크               |
| 계약(Contracts)                   | `docs/contracts/`         | 기계 검증 가능한 DB, API, 메시지 스키마(schema) |
| 아키텍처 결정(Architecture Decisions) | `docs/ADR/`               | 오래 유지되는 결정, 대안, 결과                 |
| 실행 단위(Execution Tasks)          | `docs/Task/`              | AI가 실행 가능한 작업 문서와 상태               |
| 검증 증적(Verification Evidence)    | `docs/Test/`              | 실제 명령, 결과, 수동 확인, 공백               |
| 인계 기록(Handover History)         | `docs/History/`           | 변경 요약, 링크, 리스크, 후속 작업              |

## 규칙

- 개념 영역마다 권위 있는 문서는 하나만 둔다. 이 파일을 먼저 갱신하지 않고 병렬 PRD(Product Requirements Document), 로드맵(roadmap), 아키텍처 문서를 만들지 않는다.
- `docs/contracts/`를 DB/API/message 정의의 단일 기준(source of truth)으로 둔다. 아키텍처 문서는 스키마(schema) 상세를 복제하지 않고 계약 문서로 연결한다.
- repo-local Task 문서는 한글 제목과 한글 설명 파일명 slug를 사용한다.
- 완료 선언 전에 실제 명령 또는 명시적인 수동 확인 방법으로 검증을 기록한다.
- 임시 진행 메모는 `docs/Task/`, PR(Pull Request), 이슈(issue) 댓글, `docs/History/`에 둔다. Product 또는 Architecture 문서에 구현 로그를 늘리지 않는다.

## Superpowers 호환 규칙

이 저장소는 대화, 브레인스토밍(Brainstorming), 계획, 리뷰(review), 검증 절차에 `superpowers` skill을 사용할 수 있다. 다만 저장되는 산출물(artifact)은 위의 `goodjoon-workflow` 위치를 따라야 한다.

- `docs/superpowers/specs/` 또는 병렬 명세(spec)/계획(plan) 디렉터리를 만들지 않는다.
- 브레인스토밍 설계 근거는 `docs/Task/`의 설계 메모, 관련 단일 기준(source of truth) 문서, `docs/ADR/`, 또는 `docs/History/`에 저장한다.
- 구현 계획(implementation plan)은 GitHub Issue 또는 `docs/Task/` 문서로 저장한다.
- 검증 증적(verification evidence)은 `docs/Test/`에 저장한다.
- 인계(handoff)와 오래 유지되는 변경 요약은 `docs/History/`에 저장한다.
- `superpowers` skill이 기본 경로에 설계(design), 명세(spec), 계획(plan) 문서 작성을 지시하면, 해당 경로를 일치하는 goodjoon-workflow 위치로 재지정한다.
- 기존 `superpowers` 산출물이 있으면 유효한 내용을 `docs/Task/`, `docs/Test/`, `docs/History/`, 제품(Product), 아키텍처(Architecture), 계약(Contracts), ADR(Architecture Decision Record) 중 맞는 곳으로 이관한 뒤 병렬 산출물을 제거한다.
