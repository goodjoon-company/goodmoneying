# 2026-06-20 dev.sh Vite 운영자 토큰 E2E 검증

## 대상

- 백필 계획 생성 팝업의 확인 버튼 클릭 시 `/v1/backfill/plans` 요청이 401 Unauthorized 로 실패하던 문제
- `./dev.sh app start web` 경로에서 브라우저 번들에 `VITE_OPERATOR_TOKEN`이 전달되는지 확인

## 원인

- API 쓰기 엔드포인트(endpoint)는 `X-Operator-Token` 헤더를 요구한다.
- `dev.sh`는 API/Worker에는 `GOODMONEYING_OPERATOR_TOKEN`을 전달했지만 Web에는 `VITE_OPERATOR_TOKEN`을 전달하지 않았다.
- Vite 개발 서버로 뜬 브라우저 번들은 운영자 토큰을 알 수 없어 백필 계획 생성, 수집 대상 저장, 백필 승인 같은 쓰기 요청에서 401이 발생할 수 있었다.
- 추가 검증 중 `npm run dev` 래퍼(wrapper)로 띄운 Vite 프로세스의 추적 PID(Process ID)가 불안정하고, Vite가 `CI=true`가 아닐 때 `stdin` 종료를 서버 종료로 처리하는 경로도 확인했다.
- `npm test` 또는 Playwright 실행 시 기존 웹 프로세스가 같은 프로세스 그룹(process group)에 남아 있으면 함께 종료될 수 있어, 개발 서버 프로세스를 별도 세션(session)으로 분리할 필요가 있었다.

## 변경

- `dev.sh`의 `start_web()`에서 `VITE_OPERATOR_TOKEN="$OPERATOR_TOKEN"`을 전달한다.
- Web은 `npm run dev` 래퍼 대신 `scripts/dev-vite-server.mjs`에서 Vite Node API로 직접 실행한다.
- `scripts/dev-start-background.py`가 앱 프로세스를 `start_new_session=True`로 띄워 테스트 러너의 프로세스 그룹 정리와 분리한다.
- `tests/scripts/test_dev_script.py`에 `start_web()`이 Vite 개발 서버로 운영자 토큰을 넘기고, 추적 가능한 백그라운드 프로세스로 실행되는지 확인하는 회귀 테스트(regression test)를 추가했다.

## 검증 결과

| 명령 | 결과 | 메모 |
|---|---:|---|
| `uv run pytest tests/scripts/test_dev_script.py -q` | Pass | 9 passed. 수정 전에는 신규 테스트가 실패해 401 원인을 재현했다. |
| `uv run pytest -q` | Pass | 73 passed, 1 warning. |
| `npm test` | Pass | Vitest 2 files, 7 tests passed. |
| `npm run build` | Pass | TypeScript 빌드와 Vite production build 통과. |
| `uv run ruff check . && uv run mypy apps packages tests && git diff --check` | Pass | 정적 검사(static check) 통과. |
| 격리 PostgreSQL(tmpfs) + `dev.sh app start all` + `npm run e2e` | Pass | Playwright Chromium 1 test passed. 백필 계획 생성, 승인, 수집 대상 저장, 시장 리스트, 상세 차트 흐름 확인. |
| 기본 로컬 앱 + `npm run e2e` | Pass | `http://127.0.0.1:5173`에서 Playwright Chromium 1 test passed. |

## 브라우저 E2E 환경

- PostgreSQL: 임시 Podman 컨테이너, tmpfs 데이터 디렉터리
- API: `http://127.0.0.1:28000`
- Web: `http://127.0.0.1:25173`
- Worker: fixture client, `GOODMONEYING_LIVE_UPBIT=0`
- 실행 방식: `GOODMONEYING_DEV_DIR`를 임시 디렉터리로 지정해 사용자 로컬 `.dev` 상태와 분리

## 로컬 개발 서버 조치

- 사용자 기본 로컬 웹 프로세스도 `./dev.sh app restart web`로 재시작해 새 환경변수 주입, Vite 실행 방식, 백그라운드 세션 분리를 반영했다.
- API와 Worker는 기존 프로세스를 유지했다.
