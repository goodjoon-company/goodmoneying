# goodmoneying

goodmoneying은 개인용 투자 데이터 플랫폼이다. 현재 구현된 M1은 업비트(Upbit) KRW 마켓 수집 운영 MVP(Minimum Viable Product)로, 수집 워커(Collection Worker), 운영 서버(Operations Server), React 운영 화면, DB 계약(Contract), 자동화 테스트를 포함한다.

## 문서

- 문서 지도: `docs/README.md`
- 제품 기준: `docs/01_Product.md`
- 아키텍처 기준: `docs/02_Architecture.md`
- M1 사용 설명서: `docs/사용설명서-M1-업비트-수집-운영-mvp.md`
- Repo-local agent rules: `AGENTS.md`

## 로컬 실행

```bash
uv sync
npm install
./dev.sh
./dev.sh infra start
./dev.sh app start api
./dev.sh app start web
```

- API: `http://127.0.0.1:8000`
- 운영 화면: `http://127.0.0.1:5173`
- 기본 운영 토큰(Authentication): `local-dev-token`

`./dev.sh`는 파라미터가 없으면 사용법을 출력한다. infra는 Podman Compose로 PostgreSQL을 관리하고, app은 로컬 개발 프로세스로 API, web, worker를 개별 start/stop/status 할 수 있다.

```bash
./dev.sh status
./dev.sh infra status
./dev.sh app status
./dev.sh app start api
./dev.sh app stop api
./dev.sh app restart web
./dev.sh app start worker
./dev.sh logs api
```

API는 기본적으로 `GOODMONEYING_DATABASE_URL=postgresql://goodmoneying:goodmoneying@127.0.0.1:5432/goodmoneying`을 사용한다. 이 값이 없으면 애플리케이션 코드가 fixture 저장소로 떨어질 수 있으므로, 실제 개발 동작 확인은 `./dev.sh infra start` 이후 `./dev.sh app start api`로 실행한다.

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

실제 업비트 API 호출은 기본 테스트에 포함하지 않는다. 기본 수집 검증은 fixture 기반이며, 실제 API 호출은 `GOODMONEYING_LIVE_UPBIT=1` 프로필(profile)로 분리한다.
