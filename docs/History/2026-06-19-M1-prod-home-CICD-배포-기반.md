# M1 prod-home CI/CD 배포 기반

Date: 2026-06-19
Related Task: `docs/Task/M1-T04-2026-06-19-001-운영계-prod-home-CICD-배포-설계.md`
Related ADR: `docs/ADR/ADR-0004-prod-home-CICD와-배포-프로필.md`
Related Test: `docs/Test/2026-06-19-M1-prod-home-CICD-배포-검증.md`

## 변경 요약

- `prod-home` 배포 프로필(profile)을 추가했다.
- `release` 브랜치 push 기반 GitHub Actions 배포 워크플로우(Workflow)를 추가했다.
- private GHCR(GitHub Container Registry) 이미지 빌드와 서버별 Compose 배포 경로를 추가했다.
- worker 운영 지속 실행 모드(loop mode)를 추가했다.
- 배포 후 healthcheck와 E2E(End-to-End) 실행 경로를 추가했다.
- Web 정적 앱의 운영 API base URL을 Docker build arg로 주입하도록 배포 워크플로우를 보강했다.

## 운영 선행 조건

- Mac Mini M4 organization self-hosted runner에 `self-hosted`, `mac-mini-m4` 라벨(label)이 있어야 한다.
- 운영 서버는 Tailscale hostname으로 `Mac-Mini-M4.local`, `app-server01`, `bmax-ubuntu`가 서로 접근 가능해야 한다.
- 각 운영 서버에 Docker와 Docker Compose가 설치되어 있어야 한다.
- 각 운영 서버에 `/opt/goodmoneying/env/` 비밀값(secret) 파일을 준비해야 한다.
- private GHCR read-only pull token을 서버별 Docker에 로그인해야 한다.

## 리스크

- 실제 운영 배포는 서버별 비밀값과 GHCR pull login 준비 후 검증해야 한다.
- 로컬 검증 환경에는 Docker CLI가 없어 Docker 이미지 빌드는 GitHub Actions runner에서 첫 실행 결과를 확인해야 한다.
- DB schema 자동 migration은 이번 범위에 포함하지 않았다.
- Slack 배포 명령은 후속 Task로 남겼다.

## 후속 작업

- 운영 서버에 `/opt/goodmoneying/env/` 비밀값 파일을 준비한다.
- private GHCR read-only pull token을 서버별 Docker에 로그인한다.
- Mac Mini M4 runner에서 `release` 브랜치 첫 배포를 실행하고 GitHub Actions 로그를 `docs/Test/`에 추가 기록한다.
- 필요하면 이전 `release-{short-sha}` 태그를 입력해 수동 롤백(run deploy script with previous tag) 절차를 별도 문서화한다.
