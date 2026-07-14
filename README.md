# goodmoneying

goodmoneying은 개인용 투자 데이터 플랫폼이다. 업비트(Upbit) KRW 마켓 수집 최소 기능 제품(MVP, Minimum Viable Product)은 이미 구현됐으며, 현재는 이 데이터 기반으로 관심 코인을 비교하고 투자 후보를 좁히는 초기 구현 이후 제품화(Post-MVP Productization) 단계다.

## 문서

- 개발 사양 문서 지도: `docs/README.md`
- 도메인 용어집: `UBIQUITOUS_LANGUAGE.md`
- 제품 개발 사양: `docs/01_Product.md`
- 아키텍처 개발 사양: `docs/02_Architecture.md`
- 개발·운영 사용 안내: `docs/사용설명서-M1-업비트-수집-운영-mvp.md`
- 코인 분석 빠른 안내: [코인-분석-접속-안내.md](코인-분석-접속-안내.md)
- 시스템 관리 사용법: [코인-분석-접속-안내.md#4-시스템-관리와-집계-테이블](코인-분석-접속-안내.md#4-시스템-관리와-집계-테이블)
- Repo-local agent rules: `AGENTS.md`

## 코인 분석 빠른 시작

관심 코인을 먼저 고르고 과거 수집 범위를 준비한 뒤 `코인 분석` 메뉴에서 실시간 차트, 거래량, 보조지표, 호가와 체결 요약을 확인한다. 상세 절차와 문제 확인 방법은 [코인 분석 접속 안내](코인-분석-접속-안내.md)를 따른다.

## 로컬 실행

```bash
uv sync
npm install
cp .env.sample .env
./dev.sh
./dev.sh infra start
./dev.sh app start api
./dev.sh app start web
```

- API: `http://127.0.0.1:8000`
- 운영 화면: `http://127.0.0.1:5173`
- 기본 운영 토큰(Authentication): `local-dev-token`

`./dev.sh`는 파라미터가 없으면 사용법을 출력한다. 루트 `.env` 파일이 있으면 자동으로 읽고, 셸에서 직접 지정한 환경변수는 `.env` 값보다 우선한다. 기본값은 `.env.sample`에 있다.

infra는 Podman Compose로 PostgreSQL을 관리하고, app은 로컬 개발 프로세스로 API, web, 실시간 수집 워커, 백필 수집 워커, 캔들 집계 워커를 개별 start/stop/status 할 수 있다.

```bash
./dev.sh status
./dev.sh infra status
./dev.sh app status
./dev.sh app start api
./dev.sh app stop api
./dev.sh app restart web
./dev.sh app start realtime-collection-worker
./dev.sh app start backfill-collection-worker
./dev.sh app start candle-aggregation-worker
./dev.sh logs api
```

API는 기본적으로 `GOODMONEYING_DATABASE_URL=postgresql://goodmoneying:goodmoneying@127.0.0.1:5432/goodmoneying`을 사용한다. 이 값이 없으면 빈 SQLite 저장소로 실행되므로, 실제 개발 동작 확인은 `./dev.sh infra start` 이후 `./dev.sh app start api`로 실행한다.

`.env` 기본값:

```bash
GOODMONEYING_DATABASE_URL=postgresql://goodmoneying:goodmoneying@127.0.0.1:5432/goodmoneying
GOODMONEYING_OPERATOR_TOKEN=local-dev-token
GOODMONEYING_API_PORT=8000
GOODMONEYING_WEB_PORT=5173
GOODMONEYING_REALTIME_COLLECTION_INTERVAL_SECONDS=60
GOODMONEYING_BACKFILL_POLL_SECONDS=10
GOODMONEYING_AGGREGATION_POLL_SECONDS=5
GOODMONEYING_PYTHON_BIN=.venv/bin/python
```

## Podman Compose 실행

```bash
podman compose up --build
```

앱 컨테이너까지 모두 컨테이너로 실행해야 할 때만 사용한다. 일반 개발 중에는 infra만 Podman으로 유지하고 앱은 `./dev.sh app ...`으로 실행한다.

## 테스트

```bash
uv run pytest -q
uv run ruff check .
uv run mypy apps packages tests
npm test
npm run build
npm run e2e
```

`npm run e2e`는 루트 `.env`와 외부 데이터베이스(DB, Database)를 사용하지 않는다. Playwright가 `tests/e2e/seeded_api.py`를 통해 시험용 고정 데이터(fixture) 클라이언트(client)를 SQLite 테스트 저장소에 직접 주입한 API와 전용 웹 서버를 각각 `18000`, `15173` 포트에 시작하고 종료 시 함께 정리한다. 이미 실행 중인 환경을 검증할 때만 `E2E_SKIP_WEBSERVER=1`과 `E2E_API_BASE_URL`, `E2E_WEB_BASE_URL`, `E2E_OPERATOR_TOKEN`을 명시한다.

실제 업비트 API 호출은 기본 테스트에 포함하지 않는다. 기본 수집 검증은 테스트 코드가 시험용 고정 데이터(fixture) 클라이언트(client)를 직접 주입하는 방식으로 격리하며, 런타임 수집은 `GOODMONEYING_LIVE_UPBIT=1` 프로필(profile)만 허용한다.
