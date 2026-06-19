# M1 prod-home CI/CD 배포 검증

Date: 2026-06-19
Related Task: `docs/Task/M1-T04-2026-06-19-001-운영계-prod-home-CICD-배포-설계.md`
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

## Docker 빌드 검증 공백

아래 명령은 현재 로컬 환경에 Docker CLI가 없어 실행하지 못했다.

```bash
docker build -f apps/api/Dockerfile -t goodmoneying-api:local-verify .
docker build -f apps/worker/Dockerfile -t goodmoneying-worker:local-verify .
docker build -f apps/web/Dockerfile -t goodmoneying-web:local-verify .
```

대신 `ci.yml`과 `deploy.yml`에 Docker 빌드 명령이 포함되어 있음을 `tests/scripts/test_github_workflows.py`에서 검증한다. 실제 Docker 빌드는 Mac Mini M4 self-hosted runner에서 첫 GitHub Actions 실행 결과로 추가 기록한다.

## 운영 배포 검증 공백

운영 서버 비밀값(secret), GHCR pull token, Tailscale hostname, Docker Compose 설치 준비 후 `release` 브랜치 첫 배포 결과를 별도 검증 문서로 추가한다.

## 결론

로컬에서 가능한 Python, Web, E2E, 배포 dry-run, healthcheck dry-run 검증은 통과했다. 실제 Docker 이미지 빌드와 운영 서버 배포는 Mac Mini M4 GitHub Actions runner와 운영 서버 선행 조건 준비 후 검증해야 한다.
