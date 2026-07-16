# prod-home 배포 프로필

`prod-home`은 운영계(prod)를 홈 인프라(home)에 배포하는 프로필이다.

## 서버 역할

| 서버 | 역할 | 서비스 |
|---|---|---|
| Mac Mini M4 | infra, 배포 제어 | postgres, GitHub Actions runner |
| APP SERVER 01 | application | api, 업비트 API 게이트웨이(Upbit API Gateway), realtime-collection-worker, backfill-collection-worker, candle-aggregation-worker |
| bmax-ubuntu | web | web |

## 배포 실행 흐름

- 자동 `release` push 배포는 비활성이다. 운영자는 보호된 `release`에서 승인된 40자리 SHA를 입력해 수동 실행(`workflow_dispatch`)한다.
- workflow는 이미지 build 전에 `verify-prod-deploy-gates.sh`로 P8이 설정한 exact-SHA 배포 잠금, `main`·`release` 보호, `prod` 승인자·브랜치 제한, 두 브랜치와 입력·실행 SHA 동일성, 같은 SHA의 CI `verify` 성공을 GitHub API로 증명한다. API 오류나 증명 실패는 배포를 중단한다. 보호 설정 조회에는 저장소 Administration read 권한의 `GOODMONEYING_DEPLOY_GITHUB_TOKEN` 환경 비밀값을 사용하며 기본 `GITHUB_TOKEN`으로 대체하지 않는다.
- workflow는 검증 후 Mac Mini M4의 로그인 셸(login shell)에 SSH로 들어가 `api`, `worker`, `upbit-gateway`, `web`, `migrations` 이미지를 private GHCR(GitHub Container Registry)에 `release-{40자리-sha}` 태그(tag)로 push한다. `worker` 이미지는 세 수집·집계 워커가 함께 사용한다.
- runner에서 `deploy/scripts/deploy-profile.sh prod-home "${IMAGE_TAG}"`가 실행되고, `runner/profile.env`와 `runner/hosts.env`를 읽어 서버별 target compose 파일을 복사한 뒤 원격 `docker compose pull`과 `up -d`를 실행한다.
- 배포 후 runner에서 `deploy/scripts/healthcheck-profile.sh prod-home`이 API, 업비트 API 게이트웨이, web, PostgreSQL, 실시간 수집 워커, 백필 수집 워커 상태를 점검하고, 통과하면 운영 URL 대상으로 `npm run e2e`를 실행한다.

## 파일 실행 주체

| 경로 | 주체 | 설명 |
|---|---|---|
| `.github/workflows/deploy.yml` | GitHub Actions, Mac Mini M4 runner | 승인 SHA를 수동 입력받아 실패 폐쇄형 사전점검, 이미지 빌드, 배포 스크립트 실행 |
| `deploy/scripts/*.sh` | Mac Mini M4 runner | profile을 읽고 SSH로 target 서버를 제어하는 공통 스크립트 |
| `deploy/profiles/prod-home/runner/` | Mac Mini M4 runner | runner가 읽는 profile/env/path 입력값 |
| `deploy/profiles/prod-home/target/infra/compose.yml` | Mac Mini M4 target | PostgreSQL compose 정의 |
| `deploy/profiles/prod-home/target/app/compose.yml` | APP SERVER 01 target | API, 업비트 API 게이트웨이, 수집·집계 워커 compose 정의 |
| `deploy/profiles/prod-home/target/web/compose.yml` | bmax-ubuntu target | web compose 정의 |
| `deploy/profiles/prod-home/target/*/*.sh` | target 서버 | 각 서버에 복사되어 해당 서버에서 직접 start/stop 실행 |

## 비밀값

비밀값은 repo에 커밋하지 않는다. 각 서버의 goodmoneying base directory 아래 `env/`에 둔다.

- Mac Mini M4: `/Users/goodjoon/DATA/applications/goodmoneying/env/infra.env`
- APP SERVER 01: `/home/goodjoon/project/goodmoneying/env/app.env`
- bmax-ubuntu: `/home/goodjoon/applications/goodmoneying/env/web.env`

## 서버별 host volume 경로와 설정 파일

컨테이너의 데이터 디렉터리(data directory), 캐시(cache), 서버별 설정 디렉터리(configuration directory)는 host 경로에 bind mount한다. 애플리케이션 로그(application log)는 파일 mount가 아니라 stdout/stderr 컨테이너 로그(container log)로 남긴다. 서버별 host 경로는 [runner/hosts.env](./runner/hosts.env)에서 바꾼다.

| 서버 | 설정 키 | 기본 host 경로 | 컨테이너 경로 |
|---|---|---|---|
| Mac Mini M4 | `GOODMONEYING_INFRA_BASE_DIR` | `/Users/goodjoon/DATA/applications/goodmoneying` | compose/env 파일 위치 |
| Mac Mini M4 | `GOODMONEYING_INFRA_POSTGRES_DATA_DIR` | `/Users/goodjoon/DATA/applications/goodmoneying/infra/postgres-data` | `/var/lib/postgresql/data` |
| Mac Mini M4 | `GOODMONEYING_INFRA_CONFIG_DIR` | `/Users/goodjoon/DATA/applications/goodmoneying/infra/config` | 별도 서비스에서 필요 시 사용 |
| APP SERVER 01 | `GOODMONEYING_APP_BASE_DIR` | `/home/goodjoon/project/goodmoneying` | compose/env 파일 위치 |
| APP SERVER 01 | `GOODMONEYING_APP_API_DATA_DIR` | `/home/goodjoon/project/goodmoneying/app/api-data` | `/var/lib/goodmoneying/api` |
| APP SERVER 01 | `GOODMONEYING_APP_REALTIME_COLLECTION_WORKER_DATA_DIR` | `/home/goodjoon/project/goodmoneying/app/realtime-collection-worker-data` | `/var/lib/goodmoneying/realtime-collection-worker` |
| APP SERVER 01 | `GOODMONEYING_APP_BACKFILL_COLLECTION_WORKER_DATA_DIR` | `/home/goodjoon/project/goodmoneying/app/backfill-collection-worker-data` | `/var/lib/goodmoneying/backfill-collection-worker` |
| APP SERVER 01 | `GOODMONEYING_APP_CANDLE_AGGREGATION_WORKER_DATA_DIR` | `/home/goodjoon/project/goodmoneying/app/candle-aggregation-worker-data` | `/var/lib/goodmoneying/candle-aggregation-worker` |
| APP SERVER 01 | `GOODMONEYING_APP_CONFIG_DIR` | `/home/goodjoon/project/goodmoneying/app/config` | `/etc/goodmoneying` |
| bmax-ubuntu | `GOODMONEYING_WEB_BASE_DIR` | `/home/goodjoon/applications/goodmoneying` | compose/env 파일 위치 |
| bmax-ubuntu | `GOODMONEYING_WEB_NGINX_CACHE_DIR` | `/home/goodjoon/applications/goodmoneying/web/nginx-cache` | `/var/cache/nginx` |
| bmax-ubuntu | `GOODMONEYING_WEB_CONFIG_DIR` | `/home/goodjoon/applications/goodmoneying/web/config` | `/etc/goodmoneying` |

`deploy-profile.sh`는 배포 시 각 서버의 base directory에 `deploy.hosts.env`, `deploy.compose.env`, compose 파일을 복사한다. `deploy.compose.env`에는 host 경로와 마지막 배포 이미지 태그(image tag)가 함께 저장되므로, 이후 start/stop 스크립트는 별도 환경변수(environment variable) 입력 없이 `docker compose --env-file {base}/deploy.compose.env`를 실행한다.

`application.yml`, `logback.yml`처럼 운영 서버에서 바뀔 수 있는 설정 파일은 이미지(image)에만 두지 않는다. 기본값은 이미지에 포함하되, 운영에서 바꾸는 파일은 host의 config 디렉터리에 두고 read-only mount로 컨테이너에 제공한다. 현재 goodmoneying 앱의 주요 운영 설정은 각 서버의 `{base}/env/*.env`로 관리하며, 향후 파일 기반 설정을 읽는 런타임을 추가하면 `/etc/goodmoneying`을 읽도록 앱 실행 옵션을 연결한다.

## 서버별 env 파일

아래 값은 형식 예시다. 실제 운영 값은 별도로 생성하고 배포 전 회전(rotate)한다.
배포 스크립트는 각 서버의 `{base}/env/{infra,app,web}.env.sample`도 함께 복사한다. 샘플은 새 환경변수 확인용이며, 실제 `{base}/env/*.env` 파일을 덮어쓰지 않는다.

### Mac Mini M4: `/Users/goodjoon/DATA/applications/goodmoneying/env/infra.env`

```bash
POSTGRES_DB=goodmoneying
POSTGRES_USER=goodmoneying
POSTGRES_PASSWORD=prod-home-example-postgres-password-rotate
```

### APP SERVER 01: `/home/goodjoon/project/goodmoneying/env/app.env`

```bash
GOODMONEYING_DATABASE_URL=postgresql://goodmoneying:prod-home-example-postgres-password-rotate@100.107.98.22:5432/goodmoneying
GOODMONEYING_RUNTIME_MODE=production
GOODMONEYING_RELEASE_SHA=replace-with-approved-40-character-commit-sha
GOODMONEYING_OPERATOR_TOKEN=prod-home-example-operator-token-rotate
GOODMONEYING_DASHBOARD_REFRESH_CONFIG=/etc/goodmoneying/operations-api.yaml
GOODMONEYING_LIVE_UPBIT=1
GOODMONEYING_REALTIME_COLLECTION_INTERVAL_SECONDS=60
GOODMONEYING_MARKET_SYNC_INTERVAL_SECONDS=300
GOODMONEYING_BACKFILL_POLL_SECONDS=10
GOODMONEYING_BACKFILL_BATCH_SIZE=3000
GOODMONEYING_LOG_LEVEL=INFO
UPBIT_GATEWAY_BASE_URL=https://api.upbit.com
UPBIT_GATEWAY_TIMEOUT_SECONDS=10
UPBIT_GATEWAY_ALLOWED_ORIGINS=http://100.68.208.102:8080
UPBIT_ACCESS_KEY_FILE=/etc/goodmoneying/upbit-access-key
UPBIT_SECRET_KEY_FILE=/etc/goodmoneying/upbit-secret-key
```

### bmax-ubuntu: `/home/goodjoon/applications/goodmoneying/env/web.env`

```bash
GOODMONEYING_WEB_INTERNAL_URL=http://100.68.208.102:8080
GOODMONEYING_API_INTERNAL_URL=http://100.115.38.59:8000
GOODMONEYING_UPBIT_GATEWAY_INTERNAL_URL=http://100.115.38.59:8001
GOODMONEYING_OPERATOR_TOKEN=prod-home-example-operator-token-rotate
```

Web 정적 앱의 API base URL은 `/api`로 빌드한다. Nginx 프록시(proxy)는 런타임(runtime) `GOODMONEYING_API_INTERNAL_URL`과 `GOODMONEYING_UPBIT_GATEWAY_INTERNAL_URL`로 API 서버와 업비트 API 게이트웨이에 각각 전달하고, `GOODMONEYING_OPERATOR_TOKEN`을 `X-Operator-Token` 헤더(header)로 붙인다. 브라우저 번들(bundle)과 URL에는 운영 토큰을 포함하지 않는다.

게이트웨이는 `100.115.38.59:8001`에 바인딩하고 `UPBIT_GATEWAY_ALLOWED_ORIGINS`에 실제 웹 출처만 명시한다. 키 파일은 APP SERVER 01의 `GOODMONEYING_APP_CONFIG_DIR` 아래에 소유자 읽기 전용(`chmod 400`)으로 두며 이미지·저장소·웹 서버로 복사하지 않는다. 전달 `Host`는 출처 인증 근거로 사용하지 않는다.

## GHCR pull 로그인

각 운영 서버에서 private GHCR(GitHub Container Registry) 이미지를 pull하려면 read-only 권한 토큰을 사용해 로그인한다.

```bash
printf '%s' "$CR_PAT" | docker login ghcr.io -u goodjoon-company --password-stdin
```

Mac Mini M4 runner는 macOS Keychain 접근 제한을 피하기 위해 아래 Docker config 경로를 사용한다. `.github/workflows/deploy.yml`의 `DOCKER_CONFIG`도 이 경로로 고정한다.

```bash
DOCKER_CONFIG=/Users/goodjoon/DATA/applications/goodmoneying/.docker docker login ghcr.io -u goodjoon-company
```

자동 배포와 target-local start/stop도 서버별 Docker config 경로를 명시해서 실행한다. 경로는 [runner/hosts.env](./runner/hosts.env)의 `GOODMONEYING_*_DOCKER_CONFIG`에서 관리한다.

| 서버 | Docker config 경로 |
|---|---|
| Mac Mini M4 | `/Users/goodjoon/DATA/applications/goodmoneying/.docker` |
| APP SERVER 01 | `/home/goodjoon/.docker` |
| bmax-ubuntu | `/home/goodjoon/.docker` |

Mac Mini M4는 `DOCKER_CONFIG`를 app 전용 경로로 바꾸면 Docker Desktop의 compose 플러그인(plugin)을 자동 탐색하지 못할 수 있다. `deploy-profile.sh`는 배포 전 `/Users/goodjoon/DATA/applications/goodmoneying/.docker/cli-plugins/docker-compose` symlink를 Docker Desktop 번들 compose로 보장한다.

APP SERVER 01의 컨테이너 내부에서는 `.local` mDNS hostname이 해석되지 않을 수 있다. `GOODMONEYING_DATABASE_URL`은 Postgres가 바인드된 Tailscale IP를 사용한다.

모든 서버는 Tailscale 내부 주소로 서로 접근 가능해야 한다.

## 수동 dry-run

```bash
GOODMONEYING_DEPLOY_DRY_RUN=1 deploy/scripts/deploy-profile.sh prod-home release-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
GOODMONEYING_DEPLOY_DRY_RUN=1 deploy/scripts/start-profile.sh prod-home
GOODMONEYING_DEPLOY_DRY_RUN=1 deploy/scripts/stop-profile.sh prod-home
```

## 수동 start/stop

최초 배포 후에는 target 서버마다 `{base}/deploy.compose.env`가 남는다. 이 파일에 마지막 배포 이미지 태그(image tag)가 저장되므로 수동 start/stop 시 `GOODMONEYING_IMAGE_TAG`를 다시 입력하지 않는다.

Runner에서 전체 profile을 제어할 때는 아래 명령을 쓴다.

```bash
deploy/scripts/start-profile.sh prod-home
deploy/scripts/stop-profile.sh prod-home
```

각 서버에 직접 들어가서 해당 서버의 서비스만 제어해야 하면 target-local 스크립트를 쓴다. 이 스크립트들은 배포 시 각 서버의 base directory로 복사된다.

Mac Mini M4:

```bash
cd /Users/goodjoon/DATA/applications/goodmoneying
./start.sh
./stop.sh
```

APP SERVER 01:

```bash
cd /home/goodjoon/project/goodmoneying
./start.sh
./stop.sh
./start-api.sh
./stop-api.sh
./start-realtime-collection-worker.sh
./stop-realtime-collection-worker.sh
./start-backfill-collection-worker.sh
./stop-backfill-collection-worker.sh
```

bmax-ubuntu:

```bash
cd /home/goodjoon/applications/goodmoneying
./start.sh
./stop.sh
```
