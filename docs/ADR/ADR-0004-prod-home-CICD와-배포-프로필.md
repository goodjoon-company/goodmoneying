# ADR-0004-prod-home CI/CD와 배포 프로필

Status: Accepted
Date: 2026-06-19
미래 결정 게이트 정제(Future Gate Refined By): `docs/ADR/ADR-0007-Post-MVP-아키텍처-결정-게이트.md`

## 맥락

goodmoneying MVP(Minimum Viable Product)는 개인용 투자 데이터 플랫폼으로 시작한다. 현재 운영 대상은 집에 있는 홈 인프라(home)이며, 제품 접근은 Tailscale 내부망 전용이다. 개발 PC는 개발용으로만 사용하고, 운영 서비스는 별도 서버에서 실행해야 한다.

이 결정을 작성할 당시 M1 아키텍처는 수집 워커(Collection Worker), 운영 서버(Operations Server), 운영 화면, PostgreSQL 단일 저장소로 구성됐다. 단일 인스턴스 배포 결정은 현재도 유효하며, 수평 확장(Horizontal Scaling), 고가용성(High Availability), 메시지 큐(Message Queue)의 현재 진입 조건은 `ADR-0007`을 따른다.

운영 서버는 `bmax-ubuntu`, `APP SERVER 01`, `Mac Mini M4`를 사용한다. `Mac Mini M4`에는 GitHub organization self-hosted runner가 이미 있고, infra 서버 역할과 배포 제어 역할을 맡기기에 적합하다.

## 결정

운영계(prod) 홈 인프라(home) 배포 대상은 `prod-home` 배포 프로필(Deployment Profile)로 정의한다.

배포 환경(Deployment Environment), 배포 인프라(Deployment Infrastructure), 배포 프로필(Deployment Profile)을 분리한다.

| 축 | 이번 값 | 의미 |
|---|---|---|
| 배포 환경(Deployment Environment) | `prod` | 운영계, 실제 운영 데이터 사용 |
| 배포 인프라(Deployment Infrastructure) | `home` | 집에 있는 서버 묶음 |
| 배포 프로필(Deployment Profile) | `prod-home` | `prod`와 `home`을 묶은 실제 배포 대상 |

`release` 브랜치 push가 `prod-home` 자동 배포의 유일한 기본 트리거(trigger)다. `main`과 PR(Pull Request)은 배포하지 않고 CI(Continuous Integration) 검증만 수행한다.

GitHub Actions는 Mac Mini M4의 organization self-hosted runner에서 실행한다. runner는 private GHCR(GitHub Container Registry)에 `api`, `worker`, `web` 이미지를 push하고, Tailscale 내부망을 통해 각 서버에 배포한다.

서버별 역할은 아래와 같이 정한다.

| 서버 | 역할 | 실행 서비스 |
|---|---|---|
| Mac Mini M4 | infra 서버, 배포 제어 노드 | `postgres`, GitHub Actions runner |
| APP SERVER 01 | application 서버 | `api`, `worker` |
| bmax-ubuntu | web 서버 | `web` |

운영 접근은 Tailscale 내부망 전용으로 유지한다. 외부 공개 도메인, 공인 TLS(TLS), 다중 사용자 권한(Authorization)은 이번 배포 설계 범위에 포함하지 않는다.

Slack 채팅을 통한 배포 요청은 후속 확장으로 둔다. 단, GitHub Actions 수동 실행(`workflow_dispatch`)은 향후 Slack 봇이 호출할 수 있는 진입점으로 유지한다.

## 대안

| 대안 | 장점 | 단점 |
|---|---|---|
| Mac Mini M4 runner + private GHCR + `prod-home` 프로필 | 빌드 결과가 이미지 태그(tag)로 남고, 운영 서버별 빌드 부하가 없으며, 향후 `staging-home`, `prod-aws` 같은 프로필 확장이 쉽다. | GHCR 인증과 운영 서버 pull token 관리가 필요하다. |
| Mac Mini M4 runner에서 빌드 후 `docker save/load`로 서버 전송 | 외부 registry 의존이 없고 내부망 중심으로 동작한다. | 이미지 이력, 롤백(rollback), 서버별 전송 스크립트가 복잡해진다. |
| 각 운영 서버가 git pull 후 직접 build | registry 구성이 단순하고 초기 스크립트가 짧다. | 서버별 빌드 결과가 달라질 수 있고, `bmax-ubuntu`처럼 낮은 사양 서버에 빌드 부하가 걸린다. |
| GitHub-hosted runner에서 SSH로 홈 서버 배포 | GitHub runner 관리가 필요 없다. | Tailscale 내부망 접근과 운영 비밀값(secret) 관리가 더 복잡하고, 홈 인프라 전용 배포 제어 노드 장점이 줄어든다. |

## 결과

- `release` 브랜치 push는 항상 `prod-home`에 자동 배포한다.
- `main`과 PR은 CI 검증만 수행한다.
- 배포 프로필 파일은 `deploy/profiles/prod-home/` 아래에 둔다.
- 배포 스크립트는 프로필(profile)을 입력받는 구조로 작성하되, 첫 구현에서는 `prod-home`만 허용한다.
- 이미지 태그는 `release-{short-sha}` 불변 태그를 기본으로 한다.
- 운영 서버의 GHCR pull 권한은 read-only 토큰(token) 또는 동등한 최소 권한으로 제한한다.
- 비밀값은 GitHub Actions 로그에 노출하지 않으며, 서버 로컬 env 파일 또는 GitHub Secrets를 최소 범위로 사용한다.
- DB/API/message 계약 자체는 이번 결정으로 변경하지 않는다.

## 후속 작업

- `deploy/profiles/prod-home/`과 서버별 compose 파일을 추가한다.
- `ci.yml`과 `deploy.yml`을 추가한다.
- `deploy/scripts/deploy-profile.sh`와 `deploy/scripts/healthcheck-profile.sh`를 작성한다.
- 실시간 수집 워커와 백필 수집 워커 운영 실행 모드가 지속 실행인지 확인하고, 운영용 command 또는 재시작 정책을 조정한다.
- 배포 후 healthcheck와 Tailscale 내부 URL 기준 E2E(End-to-End) 테스트를 자동화한다.
- 향후 `staging-home`, `prod-aws`, `dev-aws` 같은 대상이 생기면 별도 배포 프로필과 Task로 추가한다.
- Slack 배포 명령은 별도 Task로 설계하고, `workflow_dispatch` 호출 방식으로 연결한다.
