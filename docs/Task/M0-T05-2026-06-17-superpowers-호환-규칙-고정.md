# M0-T05-2026-06-17-superpowers-호환-규칙-고정

Status: Done
Created: 2026-06-17
Updated: 2026-06-17
Owner: Codex

## 목표

`goodjoon-workflow`와 `superpowers` skill의 문서 저장 방식 충돌을 goodjoon-workflow 방식으로 고정한다.

## 요구사항 링크

- User request: `goodjoon-workflow` 스킬에 superpowers 호환 규칙 추가
- User request: 현재 repo의 `AGENTS.md`에 호환 규칙 추가
- User request: 기존 `docs/superpowers/specs/` 산출물 정리
- Workflow: `/Users/goodjoon/.codex/skills/goodjoon-workflow/SKILL.md`
- Repo policy: `AGENTS.md`

## 범위

- 포함: 전역 `goodjoon-workflow` 스킬에 superpowers 호환 규칙 추가
- 포함: `AGENTS.md`에 repo-local superpowers 호환 규칙 추가
- 포함: 기존 superpowers 설계 근거를 `docs/History/`로 흡수
- 포함: `docs/01_Product.md`, Task, Test, History의 참조 경로 정리
- 제외: 제품 요구사항 자체 변경, 아키텍처(Architecture) 변경, 계약(Contract) 변경, 코드 구현

## 현재 맥락

`superpowers:brainstorming`은 기본적으로 설계 문서를 별도 spec 디렉터리에 저장하도록 안내한다. 이 저장 방식은 goodmoneying의 `AGENTS.md`가 정의한 Product, Architecture, Contracts, ADR, Task, Test, History 중심 source of truth 구조와 충돌한다.

## 설계 메모

- repo-local `AGENTS.md`가 최종 우선순위를 갖는다.
- `superpowers`의 대화형 질문, 설계 검토, 계획 수립 절차는 사용할 수 있다.
- 저장되는 산출물은 goodjoon-workflow 문서 위치로 라우팅한다.
- 브레인스토밍 설계 근거는 별도 spec 디렉터리에 두지 않고 `docs/Task/`, `docs/History/`, `docs/ADR/` 중 성격에 맞는 곳에 둔다.
- 기존 두 설계 명세는 `docs/History/2026-06-17-superpowers-설계근거-흡수.md`로 흡수하고 삭제한다.

## 계약 링크

- 계약 변경 없음

## 완료 기준

- 전역 `goodjoon-workflow` 스킬에 superpowers 호환 규칙이 있다.
- `AGENTS.md`에 superpowers 호환 규칙이 있다.
- `docs/superpowers/specs/` 아래 설계 파일이 남아 있지 않다.
- 제품 요구사항의 관련 근거가 `docs/History/`로 연결된다.
- 검증 증적과 인계 기록이 남아 있다.

## 검증

- `rg -n "Superpowers 호환|docs/superpowers/specs|superpowers.*호환" /Users/goodjoon/.codex/skills/goodjoon-workflow/SKILL.md AGENTS.md`
- `test -z "$(find docs/superpowers -type f 2>/dev/null)"`
- `rg -n "docs/superpowers/specs" docs AGENTS.md`
- `rg -n "2026-06-17-superpowers-설계근거-흡수|GM-PROD-001|GM-PROD-018" docs/01_Product.md docs/History/2026-06-17-superpowers-설계근거-흡수.md`
- `! rg -n "TBD|TODO" AGENTS.md docs/History/2026-06-17-superpowers-설계근거-흡수.md`
- `git diff --check`

## 추적성

- Related Test: `docs/Test/2026-06-17-superpowers-호환-규칙-검증.md`
- Related History: `docs/History/2026-06-17-superpowers-호환-규칙-고정.md`
