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
./dev.sh app start upbit-gateway
./dev.sh app start web
```

- API: `http://127.0.0.1:8000`
- 운영 화면: `http://127.0.0.1:5173`
- 업비트 API 게이트웨이: `http://127.0.0.1:8001`
- 기본 운영 토큰(Authentication): `local-dev-token`

`./dev.sh`는 파라미터가 없으면 사용법을 출력한다. 루트 `.env` 파일이 있으면 자동으로 읽고, 셸에서 직접 지정한 환경변수는 `.env` 값보다 우선한다. 기본값은 `.env.sample`에 있다.

infra는 Podman Compose로 PostgreSQL을 관리하고, app은 로컬 개발 프로세스로 API, web, 업비트 API 게이트웨이(Upbit API Gateway), 실시간 수집 워커, 백필 수집 워커, 캔들 집계 워커를 개별 start/stop/status 할 수 있다.

```bash
./dev.sh status
./dev.sh infra status
./dev.sh app status
./dev.sh app start api
./dev.sh app start upbit-gateway
./dev.sh app stop api
./dev.sh app restart web
./dev.sh app start realtime-collection-worker
./dev.sh app start backfill-collection-worker
./dev.sh app start candle-aggregation-worker
./dev.sh logs api
./dev.sh logs upbit-gateway
```

## 업비트 전체 API 작업대

`업비트 API 테스트` 아래의 `Quotation API 테스트`, `Exchange API 테스트`, `WebSocket API 테스트` 메뉴에서 공식 v1.6.3 기준 REST 51개(활성 50개·사용 중단 1개), WebSocket 스트림 14개와 `LIST_SUBSCRIPTIONS`를 탐색한다. 공통 페어를 선택하면 세 메뉴에 전달되고, 좌측에서 기능별 필수·선택 파라미터를 설정하며, 우측에서 차트·목록·호가·실시간 프레임과 원본 응답/API 출처를 확인한다. 캔들 차트의 시간축 가장자리를 이동하면 이전·다음 페이지를 이어서 조회한다.

브라우저는 Upbit 키를 갖거나 `api.upbit.com`에 직접 연결하지 않는다. 별도 게이트웨이가 브라우저 `Origin`을 제거하므로 Quotation은 공식 그룹별 초당 10회 제한을 사용한다. Exchange 기본 그룹은 포켓(Pocket)당 초당 30회, 주문·공식 주문 테스트는 초당 8회, 일괄 취소는 2초당 1회다. WebSocket은 연결 초당 5회, 연결별 메시지 초당 5회·분당 100회다. `Origin`이 Upbit까지 전달될 때 적용되는 10초당 1회 제한은 서버 게이트웨이 상향에는 적용되지 않는다.

게이트웨이의 `POST /v1/requests`와 WebSocket 연결은 `GOODMONEYING_OPERATOR_TOKEN`을 검증한다. 웹 역방향 프록시(reverse proxy)가 런타임(runtime)에 `X-Operator-Token`을 주입하며 브라우저 번들(bundle)에는 토큰을 포함하지 않는다. 8001 포트에 직접 REST 요청을 보내면 토큰 누락은 401, 오류 토큰은 403으로 상향 호출 전에 거부된다.

Exchange 읽기와 비공개 WebSocket을 시험하려면 저장소 밖의 키 파일을 소유자 읽기 전용으로 준비하고 `.env`에 절대 경로만 지정한다. 값 자체를 `.env`, 명령 인자, 저장소, 브라우저에 넣지 않는다.

```bash
chmod 400 /secure/path/upbit-access-key /secure/path/upbit-secret-key
UPBIT_ACCESS_KEY_FILE=/secure/path/upbit-access-key
UPBIT_SECRET_KEY_FILE=/secure/path/upbit-secret-key
./dev.sh app start upbit-gateway
./dev.sh app start web
```

실제 주문 생성·모든 취소·자산 이전·입출금 생성/취소·트래블룰 검증은 로컬 정책으로 차단한다. 주문 가능 여부는 실제 주문을 만들지 않는 공식 `POST /v1/orders/test`만 상향 호출한다.

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
uv run mypy apps/api apps/worker apps/upbit_gateway packages/shared tests
npm test
npm run build
npm run e2e
```

`npm run e2e`는 루트 `.env`와 외부 데이터베이스(DB, Database)를 사용하지 않는다. Playwright가 `tests/e2e/seeded_api.py`를 통해 시험용 고정 데이터(fixture) 클라이언트(client)를 SQLite 테스트 저장소에 직접 주입한 API와 전용 웹 서버를 각각 `18000`, `15173` 포트에 시작하고 종료 시 함께 정리한다. 이미 실행 중인 환경을 검증할 때만 `E2E_SKIP_WEBSERVER=1`과 `E2E_API_BASE_URL`, `E2E_WEB_BASE_URL`, `E2E_OPERATOR_TOKEN`을 명시한다.

실제 업비트 API 호출은 기본 테스트에 포함하지 않는다. 기본 수집 검증은 테스트 코드가 시험용 고정 데이터(fixture) 클라이언트(client)를 직접 주입하는 방식으로 격리하며, 런타임 수집은 `GOODMONEYING_LIVE_UPBIT=1` 프로필(profile)만 허용한다.
