# 2026-07-25 P7 성능 Web Vitals 검증

## 범위

로컬 Playwright Browser E2E에서 초기 화면의 LCP(Largest Contentful Paint), CLS(Cumulative Layout Shift), INP(Interaction to Next Paint) 대체 측정값을 자동 gate로 검증한다. INP 대체값은 route 전환 완료 시간이 아니라 브라우저 Event Timing duration을 사용한다.

## 자동화

- 계약: `docs/contracts/quality/p7-quality-evidence.yaml`의 `performance.web_vitals`
- 명령: `npm run p7:web-vitals`
- 테스트: `tests/e2e/p7-performance.spec.ts`

## 기준

| 항목 | 목표 |
|---|---|
| LCP | 2.5초 이하 |
| INP proxy | 200ms 이하 |
| CLS | 0.1 이하 |

## 결과

| 명령 | 결과 |
|---|---|
| `uv run pytest tests/scripts/test_p7_performance_gates.py -q` | RED, `AssertionError: assert 'planned' == 'passed'` |
| `npm run p7:web-vitals` | PASS, `1 passed (2.4s)` |
| `npx playwright test tests/e2e/p7-performance.spec.ts` | PASS, `3 passed (4.2s)` |

측정된 browser budget은 LCP 2.5초 이하, INP proxy 200ms 이하, CLS 0.1 이하 조건을 만족했다.
