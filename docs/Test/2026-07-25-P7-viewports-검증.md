# 2026-07-25 P7 viewports 검증

## 범위

1440, 1280, 1024, 900, 760, 390, 360px 7개 viewport에서 핵심 controls와 코인 분석 화면이 본문 가로 overflow 없이 유지되는지 검증한다.

## 자동화

- 계약: `docs/contracts/quality/p7-quality-evidence.yaml`의 `accessibility.viewports`
- 명령: `npm run p7:viewports`
- 테스트: `tests/e2e/p7-accessibility.spec.ts`

## 결과

| 명령 | 결과 |
|---|---|
| `uv run pytest tests/scripts/test_p7_accessibility_gates.py -q` | RED, `AssertionError: assert 'planned' == 'passed'` |
| `npm run p7:viewports` | PASS, `1 passed (5.4s)` |
| `npx playwright test tests/e2e/p7-accessibility.spec.ts` | PASS, `2 passed (6.5s)` |

7개 viewport 모두 본문 가로 overflow 없이 핵심 controls와 코인 분석 화면을 유지했다.
