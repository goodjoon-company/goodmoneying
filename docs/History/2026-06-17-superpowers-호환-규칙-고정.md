# 2026-06-17-superpowers-호환-규칙-고정

Date: 2026-06-17
Related Task: `docs/Task/M0.md`
Related PR: 없음

## 변경 요약

`goodjoon-workflow`와 `superpowers` skill의 문서 저장 방식 충돌을 goodjoon-workflow 방식으로 고정했다. 전역 `goodjoon-workflow` 스킬에는 superpowers 호환 규칙을 추가했고, goodmoneying repo의 `AGENTS.md`에는 별도 superpowers spec/plan 디렉터리를 만들지 않는 repo-local 규칙을 추가했다.

## 영향 문서

- `/Users/goodjoon/.codex/skills/goodjoon-workflow/SKILL.md`: superpowers 호환 규칙 추가
- `AGENTS.md`: repo-local superpowers 호환 규칙 추가
- `docs/01_Product.md`: 설계 근거 링크를 History로 변경
- `docs/History/2026-06-17-superpowers-설계근거-흡수.md`: 기존 superpowers 설계 근거 흡수
- `docs/Task/M0.md`: 설계 근거 참조 경로 정리 요약
- `docs/Test/2026-06-16-제품-범위-시나리오-문서-검증.md`: 검증 대상 경로 정리
- `docs/Test/2026-06-17-제품-요구사항-source-of-truth-통합-검증.md`: 검증 대상 경로 정리

## 영향 계약

- DB/API/message 계약(Contract) 변경은 없다.

## 검증

- 전역 workflow와 repo `AGENTS.md`에 superpowers 호환 규칙이 있는지 확인했다.
- 기존 superpowers spec 파일이 제거됐는지 확인했다.
- 제품 문서의 요구사항 근거가 History로 연결되는지 확인했다.
- 문서 자리표시자와 공백 오류를 확인했다.

## 리스크

- 전역 `goodjoon-workflow` 스킬을 수정했으므로 다른 repo에서도 superpowers 사용 시 AGENTS.md 호환 규칙 추가를 요구받는다.
- superpowers plugin 자체를 수정한 것은 아니므로, plugin 업데이트와 충돌하지 않는다.

## 후속 작업

- 다른 repo에서 superpowers와 goodjoon-workflow를 함께 사용할 때 repo-local `AGENTS.md`에 같은 호환 규칙을 추가한다.
