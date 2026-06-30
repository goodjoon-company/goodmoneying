# M1 업비트 수집 운영 MVP 사용 설명서

Status: Done
Last Updated: 2026-06-18

## 대상

이 문서는 M1 업비트 수집 운영 MVP(Minimum Viable Product)를 로컬에서 실행하고 검증하는 방법을 설명한다.

## 구성

- 운영 서버(Operations Server): FastAPI, `apps/api/goodmoneying_api`
- 수집 워커(Collection Worker): Python, `apps/worker/goodmoneying_worker`
- 운영 화면: React, `apps/web`
- 공유 도메인/저장소: `packages/shared/goodmoneying_shared`
- DB 계약(Contract): `docs/contracts/db/schema.sql`
- API 계약(Contract): `docs/contracts/api/openapi.yaml`

## 설치

```bash
uv sync
npm install
npx playwright install chromium
```

## 로컬 실행

터미널 1:

```bash
npm run dev:api
```

터미널 2:

```bash
npm run dev:web
```

브라우저에서 `http://127.0.0.1:5173`을 연다.

## 운영 화면

- 운영 상태: 활성 수집 대상, 실패 실행, 지연 대상, 결측 상태, 알림 이벤트를 확인한다.
- 수집 대상: 후보 유니버스(Candidate Universe) 상위 100개 중 정확히 50개를 선택하고 저장한다.
- 백필: 활성 수집 대상에 대한 백필 작업(Backfill Job)을 `pending` 상태로 시작하고 멈춤(Pause), 중지(Stop), 이어서하기(Resume), 안전 재시작(Safe Restart) 제어를 실행한다.
- 시장 리스트: 활성 수집 대상의 현재가 스냅샷(Ticker Snapshot), 24시간 거래대금, 품질 상태를 확인한다.
- 코인 상세: 단일 거래 상품(Instrument)의 현재가, 호가 요약(Orderbook Summary), 캔들(Candle) 흐름을 확인한다.

## API

쓰기 API는 `X-Operator-Token` 헤더가 필요하다. 로컬 기본값은 `local-dev-token`이다.

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/v1/dashboard/summary
```

## 수집 워커

fixture 기반 단발 수집:

```bash
PYTHONPATH=apps/api:apps/worker:packages/shared uv run python -m goodmoneying_worker.main --once
```

실제 업비트 API 호출은 기본 자동화 테스트에 포함하지 않는다. 실제 API 호출을 실험할 때는 별도 환경에서 아래처럼 실행한다.

```bash
GOODMONEYING_LIVE_UPBIT=1 PYTHONPATH=apps/api:apps/worker:packages/shared uv run python -m goodmoneying_worker.main --once
```

## PostgreSQL 실행

런타임(runtime)은 `GOODMONEYING_DATABASE_URL`이 `postgresql://` 또는 `postgres://`로 시작하면 PostgreSQL 저장소를 사용한다. 저장소는 `docs/contracts/db/schema.sql`을 기준으로 빈 DB에 schema를 적용한다.

```bash
GOODMONEYING_DATABASE_URL=postgresql://goodmoneying:goodmoneying@localhost:5432/goodmoneying npm run dev:api
```

Docker Compose 파일도 제공한다.

```bash
docker compose up --build
```

현재 작성 환경에는 `docker` CLI(Command Line Interface)와 호스트 `psql` 명령은 없지만, standalone `docker-compose` v5.1.1을 Podman 소켓(socket)에 연결해 동일 Compose 정의를 검증했다.

```bash
DOCKER_HOST=unix://$HOME/.local/share/containers/podman/machine/podman.sock docker-compose up --build -d
DOCKER_HOST=unix://$HOME/.local/share/containers/podman/machine/podman.sock docker-compose ps
podman exec goodmoneying-postgres-1 psql -U goodmoneying -d goodmoneying -c "select count(*) from instruments;"
```

PostgreSQL 18 컨테이너는 `/var/lib/postgresql` 경로에 볼륨(volume)을 마운트한다. Compose는 `postgres` 헬스체크(Health Check)가 정상 상태가 된 뒤 API와 worker를 시작한다.

## 검증

```bash
uv run pytest -q
uv run ruff check .
uv run mypy apps packages tests
npm test
npm run build
npm run e2e
git diff --check
```

검증 증적은 `docs/Test/2026-06-18-M1-업비트-수집-운영-mvp-검증.md`를 기준으로 한다.
