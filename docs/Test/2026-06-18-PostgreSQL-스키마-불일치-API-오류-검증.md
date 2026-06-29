# 2026-06-18-PostgreSQL-스키마-불일치-API-오류-검증

Date: 2026-06-18
Related Task: `docs/Task/M2.md`
Environment: macOS, Python 3.14 가상환경(Virtual Environment), Podman PostgreSQL, FastAPI, Playwright Chromium

## 검증 대상

- 기존 PostgreSQL 개발 DB의 스키마 불일치
- API `/v1/dashboard/summary` 500 오류
- `schema.sql` 반복 적용 가능성
- PostgreSQL 저장소 초기화 시 누락 schema 보강
- `dev.sh` API 장기 실행 프로세스 안정성

## 실행 명령

| 명령 | 결과 | 메모 |
|---|---|---|
| `uv run pytest tests/scripts/test_dev_script.py tests/contracts/test_db_contract.py tests/shared/test_postgres_repository_schema.py -q` | 통과 | 9 passed |
| `uv run pytest -q` | 통과 | 37 passed, 1 warning |
| `uv run ruff check .` | 통과 | 린트(Lint) 통과 |
| `uv run mypy apps packages tests` | 통과 | 타입 검사(Type Check) 통과 |
| `npm test` | 통과 | Vitest 4개 통과 |
| `npm run build` | 통과 | Vite 빌드 통과 |
| `npm run e2e` | 통과 | Playwright E2E 1개 통과 |
| `git diff --check` | 통과 | 공백 오류 없음 |

## RED 확인

수정 전 다음 실패를 확인했다.

- `docs/contracts/db/schema.sql`이 `CREATE TABLE`과 `CREATE INDEX`를 idempotent하게 선언하지 않아 계약 테스트 실패
- `PostgresOperationsRepository._apply_schema_if_empty()`가 기존 `instruments` 테이블 존재 시 schema 적용을 건너뛰어 단위 테스트 실패
- 실제 API 로그에서 `psycopg.errors.UndefinedTable: relation "collection_plans" does not exist` 확인

## 실제 DB 검증

수정 전 실제 개발 DB 상태:

```text
instruments instruments
collection_plans None
collection_coverage_snapshots None
collection_coverage_segments None
```

수정 후 API 재시작 및 대시보드 조회:

```bash
./dev.sh app restart api
curl -fsS http://127.0.0.1:8000/v1/dashboard/summary
```

결과:

```text
HTTP 200
totals.activeTargets = 50
```

API 장기 실행 검증:

```bash
./dev.sh app start api
./dev.sh app status api
lsof -nP -iTCP:8000 -sTCP:LISTEN
```

결과:

```text
app api running endpoint=http://127.0.0.1:8000
Python ... TCP 127.0.0.1:8000 (LISTEN)
```

수정 후 실제 개발 DB 상태:

```text
instruments instruments
collection_plans collection_plans
collection_coverage_snapshots collection_coverage_snapshots
collection_coverage_segments collection_coverage_segments
```

## 미검증 항목

- 운영 배포 환경의 migration 절차는 이번 범위가 아니다.
- 기존 테이블의 컬럼 추가/변경, 제약조건 변경, 데이터 backfill이 필요한 migration은 별도 Task가 필요하다.

## 결론

기존 PostgreSQL 개발 DB에서도 API 시작 시 신규 M2 테이블이 보강되며, `/v1/dashboard/summary` 500 오류는 재현되지 않는다.
