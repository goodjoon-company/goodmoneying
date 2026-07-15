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

### DB 마이그레이션(Migration)

DB 변경의 단일 기준(source of truth)은 `docs/contracts/db/migrations/`의 버전 SQL이다. `docs/contracts/db/schema.sql`은 dbmate가 생성하고 CI가 검증하는 스키마 스냅샷(schema snapshot)이므로 직접 수정하지 않는다. 이미 공유하거나 적용한 마이그레이션 파일도 수정하지 않고 새 파일을 추가한다.

```bash
# 새 마이그레이션 파일 생성
./dev.sh db new add_example_column

# 적용 전 상태 확인
./dev.sh db status

# 미적용 변경 실행 후 schema.sql 갱신
./dev.sh db migrate

# 현재 DB에서 schema.sql만 다시 생성
./dev.sh db dump

# 개발 환경에서 최근의 안전한 down만 명시적으로 실행
./dev.sh db rollback
```

`db new`가 만든 파일의 `-- migrate:up`에 순방향 변경을, 안전하게 되돌릴 수 있을 때만 `-- migrate:down`에 역방향 변경을 작성한다. 최초 기준선(baseline)은 기존 스키마와 데이터를 보호하기 위해 rollback이 차단된다. 운영은 자동 rollback 대신 백업과 순방향 수정(forward fix)을 사용한다.

`./dev.sh app start ...`와 `app restart ...`는 `.env`의 `GOODMONEYING_DATABASE_URL`을 사용해 미적용 마이그레이션을 먼저 실행한다. 마이그레이션이 실패하면 API·웹·워커를 시작하지 않는다. 앱 런타임 자체는 DDL(Data Definition Language)을 실행하지 않는다.

dbmate는 `npm install`로 설치되는 고정 버전을 사용한다. 로컬에 `pg_dump`가 없으면 실행 중인 Docker와 고정 dbmate 이미지로 스냅샷을 생성한다. SSL을 제공하지 않는 로컬·Tailscale PostgreSQL은 URL에 `?sslmode=disable`을 명시하고, TLS(Transport Layer Security)를 제공하는 DB는 서버 정책에 맞는 `sslmode`를 사용한다.

운영 배포는 앱 서버에서 릴리스와 같은 태그의 일회성 마이그레이션 컨테이너가 성공한 뒤에만 API·워커를 갱신한다. DB 서버에는 dbmate나 마이그레이션 파일을 설치할 필요가 없지만, 연결 허용·DDL 권한·적용 전 백업은 필요하다.

API는 기본적으로 `GOODMONEYING_DATABASE_URL=postgresql://goodmoneying:goodmoneying@127.0.0.1:5432/goodmoneying?sslmode=disable`을 사용한다. 이 값이 없으면 빈 SQLite 저장소로 실행되므로, 실제 개발 동작 확인은 `./dev.sh infra start` 이후 `./dev.sh app start api`로 실행한다.

`.env` 기본값:

```bash
GOODMONEYING_DATABASE_URL=postgresql://goodmoneying:goodmoneying@127.0.0.1:5432/goodmoneying?sslmode=disable
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
