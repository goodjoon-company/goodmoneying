# 2026-06-17-superpowers-호환-규칙-검증

Date: 2026-06-17
Related Task: `docs/Task/M0-T05-2026-06-17-superpowers-호환-규칙-고정.md`
Environment: `/Users/goodjoon/project/goodjoon/goodmoneying`

## 검증 대상

- `/Users/goodjoon/.codex/skills/goodjoon-workflow/SKILL.md`
- `AGENTS.md`
- `docs/01_Product.md`
- `docs/History/2026-06-17-superpowers-설계근거-흡수.md`
- `docs/Task/M0-T05-2026-06-17-superpowers-호환-규칙-고정.md`

## 실행 명령

| 명령 | 결과 | 메모 |
|---|---|---|
| `rg -n "Superpowers 호환|docs/superpowers/specs|superpowers.*호환" /Users/goodjoon/.codex/skills/goodjoon-workflow/SKILL.md AGENTS.md` | Pass | 전역 workflow와 repo 규칙에 호환 규칙이 있음 |
| `test -z "$(find docs/superpowers -type f 2>/dev/null)"` | Pass | superpowers spec 파일이 남아 있지 않음 |
| `rg -n "docs/superpowers/specs" docs AGENTS.md` | Pass | 남은 참조는 금지/검증 맥락뿐임 |
| `rg -n "2026-06-17-superpowers-설계근거-흡수|GM-PROD-001|GM-PROD-018" docs/01_Product.md docs/History/2026-06-17-superpowers-설계근거-흡수.md` | Pass | 제품 요구사항 근거가 History로 연결됨 |
| `! rg -n "TBD|TODO" AGENTS.md docs/History/2026-06-17-superpowers-설계근거-흡수.md` | Pass | 호환 규칙과 흡수 History에 미완성 자리표시자 없음 |
| `git diff --check` | Pass | 공백 오류 없음 |

## 수동 검증

- `goodjoon-workflow` 스킬에 superpowers skill 사용 시 repo `AGENTS.md`에 호환 규칙을 먼저 추가하라는 지침이 있는지 확인했다.
- goodmoneying `AGENTS.md`가 `docs/superpowers/specs/` 생성을 금지하고 goodjoon-workflow 경로로 산출물을 라우팅하는지 확인했다.
- 삭제된 설계 명세의 핵심 결정과 대체/반영 상태가 `docs/History/2026-06-17-superpowers-설계근거-흡수.md`에 남아 있는지 확인했다.

## 미검증 항목

- 코드 변경이 없으므로 자동화된 E2E(End-to-End) 테스트는 적용하지 않았다.
- superpowers plugin 자체 동작은 수정하지 않았으므로 별도 plugin 회귀 테스트는 수행하지 않았다.

## 결론

superpowers 산출물 저장 방식은 goodjoon-workflow 방식으로 고정됐다. 이후 이 repo에서 superpowers skill을 사용해도 저장 문서는 `AGENTS.md`의 source of truth 위치를 따른다.
