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
npm run dev:api
npm run dev:web
```

- API: `http://127.0.0.1:8000`
- 운영 화면: `http://127.0.0.1:5173`
- 기본 운영 토큰(Authentication): `local-dev-token`

## Docker Compose 실행

```bash
docker compose up --build
```

현재 작성 환경에서는 standalone `docker-compose`를 Podman 소켓(socket)에 연결해 같은 Compose 정의를 검증했다.

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
