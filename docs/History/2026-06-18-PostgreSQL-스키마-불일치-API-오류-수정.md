# 2026-06-18-PostgreSQL-스키마-불일치-API-오류-수정

Date: 2026-06-18
Related Task: `docs/Task/M2.md`
Related PR: 없음

## 변경 요약

- 기존 PostgreSQL 개발 DB에서 `/v1/dashboard/summary`가 500으로 실패하는 문제를 수정했다.
- 원인은 `instruments` 테이블이 존재하면 schema 적용을 건너뛰는 초기화 로직이었다.
- M2에서 추가된 `collection_plans`, `collection_coverage_snapshots`, `collection_coverage_segments`가 기존 DB에는 없어 API 조회가 실패했다.
- `schema.sql`을 `CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS` 기반 idempotent DDL(Data Definition Language)로 변경했다.
- `PostgresOperationsRepository`가 시작 시 schema 계약을 항상 적용하도록 수정했다.
- `dev.sh`의 API/worker 장기 실행 Python 프로세스를 `uv run` wrapper 대신 `.venv/bin/python`으로 직접 실행하도록 보강했다.
- 실제 개발 DB에서 API 재시작 후 신규 테이블이 생성되고 대시보드 API가 200으로 응답함을 확인했다.

## 영향 문서

- `docs/contracts/db/schema.sql`
- `docs/contracts/db/README.md`
- `dev.sh`
- `.env.sample`
- `README.md`
- `docs/Task/M2.md`
- `docs/Test/2026-06-18-PostgreSQL-스키마-불일치-API-오류-검증.md`

## 영향 계약

- DB 계약은 반복 적용 가능한 개발 환경 DDL로 유지한다.
- 기존 테이블의 구조 변경이나 데이터 변환이 필요한 변경은 이번 방식으로 충분하지 않으며 별도 migration Task가 필요하다.

## 검증

- `uv run pytest tests/contracts/test_db_contract.py tests/shared/test_postgres_repository_schema.py -q`
- `uv run pytest -q`
- `uv run ruff check .`
- `uv run mypy apps packages tests`
- `npm test`
- `npm run build`
- `npm run e2e`
- `git diff --check`
- 실제 개발 DB에서 `./dev.sh app restart api` 후 `/v1/dashboard/summary` 200 확인
- `./dev.sh app start api` 후 `lsof -nP -iTCP:8000 -sTCP:LISTEN`으로 API 리스닝 확인

## 리스크

- `IF NOT EXISTS`는 누락 테이블과 누락 인덱스 보강에는 충분하지만, 기존 테이블의 컬럼/제약조건 변경을 자동 반영하지는 않는다.
- 운영 환경에 적용할 때는 별도 migration 체계를 도입해야 한다.

## 후속 작업

- DB 계약이 더 자주 바뀌기 시작하면 migration 디렉터리와 적용 이력 테이블을 도입한다.
- API `/health` 또는 별도 운영 endpoint에 schema version 또는 저장소 종류를 노출할지 검토한다.
