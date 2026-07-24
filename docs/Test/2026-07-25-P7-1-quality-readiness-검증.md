# 2026-07-25 P7-1 품질 readiness gate 검증

## 범위

P7 성능·접근성·보안·부하·장시간·장애·백업 복구 검증을 시작하기 위한 단일 품질 증적 매니페스트(Quality Evidence Manifest)와 CI readiness gate를 추가했다.

## RED

| 명령 | 결과 | 의미 |
|---|---|---|
| `uv run pytest tests/scripts/test_p7_quality_gates.py -q` | FAIL, `ModuleNotFoundError: No module named 'scripts.verify_p7_quality_gates'` | P7 품질 gate 스크립트와 계약이 없음을 확인 |

## GREEN

| 명령 | 결과 | 의미 |
|---|---|---|
| `uv run pytest tests/scripts/test_p7_quality_gates.py -q` | PASS, `3 passed in 0.04s` | 매니페스트 구조, CI readiness 명령, 미해결 산출물 스캐너 동작 확인 |
| `uv run python scripts/verify_p7_quality_gates.py --mode readiness --manifest docs/contracts/quality/p7-quality-evidence.yaml` | PASS, `ok=true`, `gates=14` | 현재 저장소에서 P7 readiness gate 통과 |
| `uv run ruff check --fix tests/scripts/test_p7_quality_gates.py scripts/verify_p7_quality_gates.py` | PASS, `Found 1 error (1 fixed, 0 remaining).` | import 정렬 자동 보정 뒤 lint 통과 |
| `uv run mypy scripts/verify_p7_quality_gates.py tests/scripts/test_p7_quality_gates.py` | PASS, `Success: no issues found in 2 source files` | P7 gate 스크립트와 테스트 타입 검증 |
| `uv run pytest tests/scripts/test_p7_quality_gates.py tests/scripts/test_github_workflows.py -q` | PASS, `15 passed in 0.05s` | P7 gate와 CI workflow 계약 동시 검증 |
| `uv run python scripts/verify_p7_quality_gates.py --mode release --manifest docs/contracts/quality/p7-quality-evidence.yaml` | FAIL, `P7 release gate 미통과` | P7-1이 전체 P7 완료로 오인되지 않도록 release gate가 닫혀 있음을 확인 |
| `uv run ruff check .` | PASS, `All checks passed!` | 전체 Python lint 검증 |
| `uv run mypy apps/api apps/worker apps/upbit_gateway packages/shared tests` | PASS, `Success: no issues found in 196 source files` | 전체 Python 타입 검증 |
| `git diff --check` | PASS, 출력 없음 | whitespace 오류 없음 |
| `uv run pytest -q` | PASS, `819 passed, 156 skipped, 1 warning in 62.57s` | 전체 Python 회귀 검증 |
| `npm test` | PASS, `29 passed`, `181 passed` | 전체 Web 단위 테스트 |
| `npm run build` | PASS, build 완료, 기존 Vite chunk warning 유지 | Web production build 검증 |
| `npm run e2e` | PASS, `22 passed`, API·웹 시험 서버 종료 확인 | 자동화된 Browser E2E 검증 |

## 코드 리뷰

| 항목 | 결과 |
|---|---|
| 요구사항 정합성 | P7-1은 `docs/contracts/quality/p7-quality-evidence.yaml`을 단일 품질 계약으로 두고 readiness/release mode를 분리한다. release mode 실패 테스트가 전체 P7 완료 오인을 막는다. |
| 아키텍처·계약 정합성 | Architecture 운영 gate와 `docs/contracts/README.md`가 같은 manifest 경로를 가리킨다. 계약 상세는 YAML에 두고 Architecture에는 경계만 기록했다. |
| 테스트 정합성 | RED 누락에서 시작했고, required gate·CI 경로·release 실패·미해결 산출물 scanner를 자동 테스트로 고정했다. |
| 문서 정합성 | Product, Architecture, UI QA, Task, Test, History가 P7-1은 readiness baseline이고 P7 완료가 아님을 명시한다. |
| 발견 이슈 | Critical 없음, Important 없음, Minor 없음 |

## 보정 내역

- `scripts/verify_p7_quality_gates.py`의 self-match를 피하도록 미해결 토큰 패턴을 런타임 조합으로 구성했다.
- `apps/web/src` 아래 테스트 파일(`*.test.*`)은 제품 코드 미해결 산출물 스캔에서 제외했다.
- 런타임 오류 문구에서 제품 메시지의 `mock` 표현을 제거했다.

## 아직 통과로 표시하지 않는 항목

P7-1은 readiness gate다. Web Vitals, 첫 유용 셸, 실시간 event 1초, WCAG 2.2 AA, 7개 viewport, dependency/image/secret scan, load/soak/chaos, backup/restore rehearsal은 매니페스트에 계획 상태로 등록됐고 후속 P7 조각에서 실제 증적을 만들어야 한다.
