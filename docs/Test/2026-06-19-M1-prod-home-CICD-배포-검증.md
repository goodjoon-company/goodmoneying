# M1 prod-home CI/CD 배포 검증

Date: 2026-06-19
Related Task: `docs/Task/M1.md`
Environment: macOS, Python 3.14 가상환경(Virtual Environment), Node.js 워크스페이스(Workspace), Playwright Chromium

## 검증 대상

- `release` 브랜치 push 기반 GitHub Actions 배포 워크플로우(Workflow)
- private GHCR(GitHub Container Registry) 이미지 태그(tag)와 `prod-home` 배포 프로필(profile)
- Mac Mini M4, APP SERVER 01, bmax-ubuntu 서버 역할별 Compose 배포 경로
- worker 지속 실행 모드(loop mode)
- 배포 후 healthcheck와 Tailscale 내부 URL 대상 E2E(End-to-End) 실행 모드

## 자동 검증

| 명령 | 결과 | 메모 |
|---|---|---|
| `uv run ruff check .` | PASS | 전체 Python 린트(Lint) 통과 |
| `uv run mypy apps/api apps/worker packages/shared tests` | PASS | 27개 소스 타입 검사(Type Check) 통과 |
| `uv run pytest` | PASS | 63 passed, 1 warning |
| `npm test` | PASS | Vitest 4개 테스트 통과 |
| `npm run build` | PASS | TypeScript 빌드와 Vite 빌드 통과 |
| `npm run e2e` | PASS | Playwright E2E 1개 통과 |
| `GOODMONEYING_DEPLOY_DRY_RUN=1 deploy/scripts/deploy-profile.sh prod-home release-abcdef0` | PASS | 서버별 mkdir, scp, compose pull/up 명령 출력 확인 |
| `GOODMONEYING_DEPLOY_DRY_RUN=1 deploy/scripts/healthcheck-profile.sh prod-home` | PASS | API, web, PostgreSQL, worker healthcheck 명령 출력 확인 |
| `command -v docker && docker --version` | NOT RUN | 현재 로컬 환경에 Docker CLI가 없어 이미지 빌드 검증은 GitHub Actions runner에서 확인 필요 |
| `uv run pytest tests/scripts/test_github_workflows.py tests/scripts/test_deploy_profile.py -v` | PASS | 배포 워크플로우와 `prod-home` 프로필 회귀 테스트 27개 통과 |
| `deploy/scripts/healthcheck-profile.sh prod-home` | PASS | 첫 운영 배포 후 Mac Mini PostgreSQL, APP SERVER 01 API/worker, bmax web healthcheck 통과 |
| `E2E_OPERATOR_TOKEN=*** E2E_SKIP_WEBSERVER=1 E2E_API_BASE_URL=http://app-server01:8000 E2E_WEB_BASE_URL=http://bmax-ubuntu:8080 npm run e2e` | PASS | 운영 URL 대상 Playwright E2E 1개 통과, 32.0s |

## 첫 운영 배포 확인

GitHub Actions `Deploy prod-home` run `27821382094`는 Mac Mini M4 organization self-hosted runner에서 빌드와 private GHCR push, 서버별 compose 배포까지 통과했다.

첫 healthcheck 실패 원인은 APP SERVER 01 컨테이너 내부에서 `Mac-Mini-M4.local` mDNS hostname을 해석하지 못한 것이었다. APP SERVER 01의 `/home/goodjoon/project/goodmoneying/env/app.env`에서 `GOODMONEYING_DATABASE_URL`을 Postgres가 바인드된 Tailscale IP `100.107.98.22` 기준으로 바꾸고 api/worker를 재기동하자 API와 worker가 정상 기동했다.

운영 URL E2E는 두 가지 보강이 필요했다.

- 운영 API 쓰기 요청은 `GOODMONEYING_OPERATOR_TOKEN`과 동일한 토큰을 써야 하므로 GitHub Actions가 APP SERVER 01의 `app.env`에서 값을 읽고 `E2E_OPERATOR_TOKEN`으로 마스킹(masking)해 주입한다.
- 운영 DB의 실시간 시장 순위, 표시 가능한 시장 수, API 응답 시간이 로컬 fixture와 다르므로 E2E는 특정 코인(BTC)이나 정확한 행 수 고정 대신 실제 표시 행 기준으로 탐색하고, 첫 렌더링 대기 시간을 늘린다.
- Mac Mini M4 runner의 Playwright 브라우저 캐시는 항상 존재한다고 볼 수 없으므로 배포 워크플로우에서 `npx playwright install chromium`을 실행한다.

## Docker 빌드 검증 공백

아래 명령은 현재 로컬 환경에 Docker CLI가 없어 실행하지 못했다.

```bash
docker build -f apps/api/Dockerfile -t goodmoneying-api:local-verify .
docker build -f apps/worker/Dockerfile -t goodmoneying-worker:local-verify .
docker build -f apps/web/Dockerfile -t goodmoneying-web:local-verify .
```

대신 `ci.yml`과 `deploy.yml`에 Docker 빌드 명령이 포함되어 있음을 `tests/scripts/test_github_workflows.py`에서 검증한다. 실제 Docker 빌드는 Mac Mini M4 self-hosted runner의 GitHub Actions 실행에서 API, worker, web 이미지 buildx push 단계 통과로 확인했다.

## 운영 배포 검증 공백

운영 서버 비밀값(secret), GHCR pull token, Tailscale 내부 주소, Docker Compose 설치 준비는 완료했다. 향후 남은 공백은 배포 실패 시 자동 롤백(rollback), DB migration 전략, web 정적 앱의 운영 쓰기 토큰 노출 개선이다.

## 결론

로컬에서 가능한 Python, Web, E2E, 배포 dry-run, healthcheck dry-run 검증과 운영 서버 대상 healthcheck/E2E 검증은 통과했다. 실제 Docker 이미지 빌드와 GHCR push도 Mac Mini M4 GitHub Actions runner에서 통과했다.
