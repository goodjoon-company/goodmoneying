# 개발·운영 사용 안내

Status: Maintained
Last Updated: 2026-07-16

## 이 문서가 답하는 질문

이 안내는 goodmoneying을 로컬에서 실행하고, 업비트 데이터 준비부터 관심 코인 분석·시스템 상태 확인까지 진행하는 방법을 설명한다. 제품의 범위와 완료 기준은 [제품 개발 사양](01_Product.md), 구성요소 책임은 [아키텍처 개발 사양](02_Architecture.md), 정확한 요청·응답과 저장 형식은 [계약 기준](contracts/README.md)을 따른다.

## 빠른 시작

### 1. 의존성 설치와 환경 파일 준비

```bash
uv sync
npm install
npx playwright install chromium
cp .env.sample .env
```

기본 `.env`는 로컬 PostgreSQL, API `8000`, 웹 `5173`, 운영 토큰 `local-dev-token`을 사용한다. 실제 업비트 데이터 수집은 `GOODMONEYING_LIVE_UPBIT=1`일 때만 수행한다. 공개 API를 조회하는 테스트 페이지는 별도 키 없이 사용할 수 있지만, 이는 제품 저장소에 데이터를 적재하지 않는다.

### 2. 인프라와 앱 실행

```bash
./dev.sh infra start
./dev.sh app start api
./dev.sh app start upbit-gateway
./dev.sh app start web
./dev.sh app start realtime-collection-worker
./dev.sh app start backfill-collection-worker
./dev.sh app start candle-aggregation-worker
./dev.sh status
```

브라우저는 `http://127.0.0.1:5173`, API 상태는 `http://127.0.0.1:8000/health`, 업비트 API 게이트웨이 상태는 `http://127.0.0.1:8001/health`에서 확인한다. 일반 개발에서는 PostgreSQL만 Podman Compose로 실행하고 API·웹·게이트웨이·워커는 `./dev.sh app`으로 개별 제어한다.

```bash
./dev.sh app restart api
./dev.sh app stop web
./dev.sh logs upbit-gateway
./dev.sh logs realtime-collection-worker
./dev.sh logs candle-aggregation-worker
```

`GOODMONEYING_DATABASE_URL`이 없으면 API가 빈 SQLite 저장소로 시작할 수 있다. 실제 수집·분석 흐름은 `./dev.sh infra start` 뒤 기본 PostgreSQL URL을 사용해 확인한다.

## 화면별 사용 절차

### 데이터 준비와 관심목록

1. `Backfill 관리`에서 업비트 KRW 후보 중 분석할 코인을 관심목록에 저장한다.
2. 필요한 과거 기간을 선택해 Backfill 계획을 만들고 시작한다. 1년 일봉을 확인하려면 최소 1년의 원천 데이터를 준비한다.
3. Backfill 작업은 일시정지(Pause), 재개(Resume), 중지(Stop), 안전 재시작(Safe Restart)을 지원한다. 재개는 이미 저장된 구간을 다시 요청하지 않는다.

관심목록은 운영 상태, Backfill 관리, 코인 분석에서 같은 활성 수집 대상 순서를 공유한다.

### 코인 분석

1. `코인 분석` 메뉴에서 관심 코인을 선택한다.
2. 기본 1년 일봉에서 거래량, SMA 20/60, EMA 20, 볼린저 밴드(Bollinger Bands), RSI 14와 현재가·호가·체결 요약을 확인한다.
3. 월·주·일·시·30분·10분·5분·1분 봉과 기간을 바꿔 저장된 범위를 분석한다.

차트가 비어 있으면 선택 기간의 원천 캔들 존재 여부와 Backfill 상태를 먼저 확인한다. 1분·5분·10분·30분·시봉은 최근 1,000개 캔들로 제한될 수 있으며, 장기 분석 데이터는 최신 집계 봉을 우선 사용한다. 분석 WebSocket 계약과 재연결 동작은 [코인 분석 실시간 계약](contracts/api/realtime-analysis-websocket.md)을 따른다.

### 시스템 관리와 운영 상태

- `시스템 관리`는 실시간 수집·Backfill·집계 워커의 heartbeat, 현재 대상, 데이터 유형 또는 집계 단위, 집계 진행률을 실시간으로 보여 준다.
- `운영 상태`는 수집 품질, 최신성, 결측, 실패와 저장량 변화를 확인하는 내부 진단 화면이다.
- 집계 워커는 활성 코인의 원천봉보다 오래된 집계 봉을 찾으면 자동으로 작업을 생성한다. 집계가 지연돼도 분석 조회는 원천봉 파생 보조 경로로 정확성을 유지한다.

상세 화면 순서와 장애 확인 명령은 [코인 분석 접속 안내](../코인-분석-접속-안내.md)를 참고한다.

### 업비트 전체 API 테스트

`업비트 API 테스트` 메뉴는 다음 2레벨 메뉴를 제공한다.

- `Quotation API 테스트`: REST 13개(활성 12개·사용 중단 1개)의 페어·캔들·체결·현재가·호가 그룹
- `Exchange API 테스트`: REST 38개의 포켓·계정·주문·출금·입금·Travel Rule·서비스 그룹
- `WebSocket API 테스트`: 공개 12개·비공개 2개 스트림과 `LIST_SUBSCRIPTIONS`

상단 공통 페어는 세 메뉴가 공유한다. 좌측 요청 패널은 열거형(enum)·날짜·숫자 등 타입에 맞는 입력을 제공하고, 우측 결과 패널은 차트·목록·호가·실시간 프레임과 원본 요청·응답/API 출처를 표시한다. 캔들 차트의 시간축 가장자리로 이동하면 과거 또는 최신 방향의 데이터를 중복 없이 이어 붙인다.

공개 조회는 키가 필요 없다. Exchange 읽기와 비공개 WebSocket은 저장소 밖의 읽기 전용 키 파일을 게이트웨이에 설정해야 한다.

```bash
chmod 400 /secure/path/upbit-access-key /secure/path/upbit-secret-key
# .env에는 값이 아니라 절대 경로만 둔다.
UPBIT_ACCESS_KEY_FILE=/secure/path/upbit-access-key
UPBIT_SECRET_KEY_FILE=/secure/path/upbit-secret-key
./dev.sh app restart upbit-gateway
```

실제 주문·모든 취소·자산 이전·입출금 생성/취소·트래블룰 검증은 실행 버튼이 비활성화되고 게이트웨이에서도 403으로 차단된다. 실제 주문 없이 주문 형식·가능 여부를 확인하는 공식 `POST /v1/orders/test`만 허용한다. 브라우저에는 키·JWT·운영 토큰을 저장하지 않는다. 웹 서버는 런타임 환경 변수의 운영 토큰을 같은 출처 역방향 프록시(reverse proxy)에서만 주입한다.

게이트웨이의 `POST /v1/requests`를 8001 포트로 직접 호출할 때도 `X-Operator-Token`이 필요하다. 토큰 누락은 401, 오류 토큰은 403으로 요청 본문 검증·카탈로그 조회·업비트 상향 호출 전에 거부된다. 일반 화면 사용자는 토큰을 입력하지 않으며 웹 역방향 프록시가 서버에서 주입한다.

브라우저가 Upbit를 직접 호출해 `Origin` 헤더가 전달되면 Quotation REST와 공개 WebSocket은 10초당 1회로 제한된다. 이 제품은 같은 출처 역방향 프록시(reverse proxy)와 별도 게이트웨이를 사용하고 상향 `Origin`을 제거하므로 Quotation 그룹별 초당 10회, Exchange 기본 초당 30회, 주문·주문 테스트 초당 8회, 일괄 취소 2초당 1회, WebSocket 연결 초당 5회와 연결별 메시지 초당 5회·분당 100회를 적용한다.

## API와 계약 확인

읽기 API 예시는 다음과 같다. 쓰기 API는 `X-Operator-Token` 헤더가 필요하며 로컬 기본값은 `local-dev-token`이다.

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/v1/dashboard/summary
curl http://127.0.0.1:8000/v1/market-list
```

API 경로·요청·응답은 [OpenAPI 계약](contracts/api/openapi.yaml), WebSocket 메시지는 [API 계약 안내](contracts/api/README.md), DB 테이블·제약조건은 [DB 계약](contracts/db/schema.sql)을 기준으로 한다.

## 문제 확인 순서

```bash
./dev.sh status
./dev.sh logs api
./dev.sh logs realtime-collection-worker
./dev.sh logs backfill-collection-worker
./dev.sh logs candle-aggregation-worker
```

| 증상 | 먼저 확인할 항목 | 다음 조치 |
|---|---|---|
| 차트가 비어 있음 | 관심목록 여부, 선택 기간, Backfill 상태 | 원천 캔들 범위를 준비하고 API 로그를 확인한다. |
| 현재가·호가가 오래됨 | 실시간 수집 워커 heartbeat, `GOODMONEYING_LIVE_UPBIT` | 워커 로그를 확인하고 필요한 프로세스를 재시작한다. |
| 집계 진행률이 멈춤 | 집계 워커 heartbeat, 작업 대상 실패 | 집계 워커 로그와 시스템 관리 화면의 실패 대상을 확인한다. |
| 분석 연결이 끊김 | API 프로세스, 브라우저 WebSocket 상태 | API·웹을 재시작하고 동일 선택 상태의 자동 재연결을 확인한다. |
| 업비트 작업대가 503을 표시함 | 게이트웨이 상태, 키 파일 한 쌍과 권한 | `./dev.sh logs upbit-gateway`를 확인하고 두 파일을 모두 `chmod 400`으로 설정한다. |
| 게이트웨이 직접 REST가 401·403을 표시함 | `X-Operator-Token` 누락 또는 서버 토큰과 불일치 | 일반 화면은 웹 프록시를 사용하고, 서버 간 직접 호출은 `GOODMONEYING_OPERATOR_TOKEN`과 같은 토큰을 헤더에 설정한다. |
| Exchange가 401·403을 표시함 | API Key 권한과 허용 IP | Upbit API Key 관리에서 읽기/주문 테스트 권한과 게이트웨이 서버 IP를 확인한다. |
| 429·418을 표시함 | 결과의 제한 그룹·잔여 횟수·재시도 시각 | 자동 반복 호출을 멈추고 표시된 냉각 시간이 지난 뒤 다시 실행한다. |
| WebSocket 연결이 1008로 종료됨 | 운영 토큰 주입과 명시적 출처 허용 목록 | 웹 역방향 프록시 설정과 `UPBIT_GATEWAY_ALLOWED_ORIGINS`의 실제 웹 출처를 확인한다. |

## 자동화 검증

```bash
uv run pytest -q
uv run ruff check .
uv run mypy apps/api apps/worker apps/upbit_gateway packages/shared tests
npm test
npm run build
npm run e2e
git diff --check
```

`npm run e2e`는 외부 PostgreSQL이나 루트 `.env`에 의존하지 않는다. Playwright가 시험용 SQLite 저장소, API, 전용 웹 서버를 만들고 종료 시 정리한다. 실제 배포 환경을 검증할 때만 `E2E_SKIP_WEBSERVER=1`과 대상 URL·운영 토큰을 명시한다. 최신 실제 결과는 `docs/Test/`의 해당 변경 검증 문서를 확인한다.
