# M1 prod-home CI/CD 배포 기반

Date: 2026-06-19
Related Task: `docs/Task/M1.md`
Related ADR: `docs/ADR/ADR-0004-prod-home-CICD와-배포-프로필.md`
Related Test: `docs/Test/2026-06-19-M1-prod-home-CICD-배포-검증.md`

## 변경 요약

- `prod-home` 배포 프로필(profile)을 추가했다.
- `release` 브랜치 push 기반 GitHub Actions 배포 워크플로우(Workflow)를 추가했다.
- private GHCR(GitHub Container Registry) 이미지 빌드와 서버별 Compose 배포 경로를 추가했다.
- worker 운영 지속 실행 모드(loop mode)를 추가했다.
- 배포 후 healthcheck와 E2E(End-to-End) 실행 경로를 추가했다.
- Web 정적 앱의 운영 API base URL을 Docker build arg로 주입하도록 배포 워크플로우를 보강했다.
- 운영 E2E가 APP SERVER 01의 `GOODMONEYING_OPERATOR_TOKEN`을 마스킹(masking)해 사용하도록 보강했다.
- 운영 컨테이너 내부에서 `.local` mDNS hostname이 실패할 수 있어 DB URL은 Postgres가 바인드된 Tailscale IP를 사용하도록 정리했다.
- GitHub Actions runner의 Playwright Chromium 브라우저를 배포 워크플로우에서 명시적으로 설치하도록 했다.

## 운영 선행 조건

- Mac Mini M4 organization self-hosted runner에 `self-hosted`, `mac-mini-m4` 라벨(label)이 있어야 한다.
- 운영 서버는 Tailscale 내부 주소로 서로 접근 가능해야 한다.
- 각 운영 서버에 Docker와 Docker Compose가 설치되어 있어야 한다.
- Mac Mini M4에는 `/Users/goodjoon/DATA/applications/goodmoneying/env/infra.env` 비밀값(secret) 파일을 준비해야 한다.
- APP SERVER 01에는 `/home/goodjoon/project/goodmoneying/env/app.env` 비밀값 파일을 준비해야 한다.
- bmax-ubuntu에는 `/home/goodjoon/applications/goodmoneying/env/web.env` 비밀값 파일을 준비해야 한다.
- private GHCR read-only pull token을 서버별 Docker에 로그인해야 한다.

## 리스크

- API 응답이 운영 DB 기준으로 느릴 수 있어 배포 후 E2E는 로컬 fixture보다 긴 대기 시간을 둔다.
- DB schema 자동 migration은 이번 범위에 포함하지 않았다.
- Web 정적 앱의 `VITE_OPERATOR_TOKEN` 방식은 내부망 MVP 기준이며, 후속으로 서버 경유 쓰기 인증 구조를 검토한다.
- Slack 배포 명령은 후속 Task로 남겼다.

## 후속 작업

- 필요하면 이전 `release-{short-sha}` 태그를 입력해 수동 롤백(run deploy script with previous tag) 절차를 별도 문서화한다.
- API 응답 시간 개선을 위해 dashboard/market-list 쿼리 최적화 또는 캐시(cache)를 검토한다.
