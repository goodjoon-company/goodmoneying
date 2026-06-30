# 2026-06-30 fixture 후보 차단과 live 후보 복구

## 요약

현재 후보종목 화면에 `KRW-GM###`/`굿머니코인` fixture 후보가 표시되던 문제를 복구했다. 최신 후보 유니버스(Candidate Universe)를 live 업비트 데이터로 다시 저장했고, 런타임(runtime)에서 fixture 데이터가 다시 PostgreSQL 운영 DB에 들어가지 않도록 차단했다.

## 변경 내용

- 워커 런타임의 업비트 클라이언트 선택은 `GOODMONEYING_LIVE_UPBIT=1`일 때만 `LiveUpbitClient`를 반환한다.
- `GOODMONEYING_DEMO_DATA=1` 기반 API demo fixture 저장소 경로를 런타임에서 차단했다.
- PostgreSQL 후보 유니버스 저장 전에 `KRW-GM###` 또는 `굿머니코인` fixture 후보를 감지해 DB 접속 전 `ValueError`를 발생시킨다.
- 후보 유니버스 갱신을 `collection_runs`에 `candidate_refresh/candidate_universe` 실행 이력으로 기록한다.
- 같은 초에 여러 실행 이력이 생겨도 최신 실행이 앞에 오도록 `collection_runs` 정렬에 `id DESC` 보조 기준을 추가했다.
- Playwright E2E(End-to-End) 기본 API 서버를 demo fixture 대신 `.env`의 PostgreSQL DB 기반으로 실행하도록 변경했다.
- `.env.sample`에서 더 이상 지원하지 않는 `GOODMONEYING_DEMO_DATA` 예시를 제거했다.

## 운영 복구

- live 후보 갱신 실행 결과: `refreshed=100`
- 최신 후보 시각: `2026-06-30T09:50:18+09:00`
- 후보 유니버스 fixture 개수: `0`
- 시장 목록 fixture 개수: `0`
- 후보 갱신 이력: `candidate_refresh/candidate_universe/succeeded`

## 검증

검증 증적은 `docs/Test/2026-06-30-fixture-후보-차단과-live-후보-복구-검증.md`에 기록했다.

## 후속 주의

- 테스트 격리용 fixture 직접 주입은 유지하되, 환경변수나 런타임 기본값으로 fixture를 선택하는 경로는 다시 만들지 않는다.
- E2E는 실제 PostgreSQL 기반 API를 사용하므로 로컬 검증 전 `./dev.sh infra start`와 DB 상태를 먼저 확인해야 한다.
