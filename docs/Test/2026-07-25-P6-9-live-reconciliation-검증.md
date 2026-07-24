# 2026-07-25 P6-9 live 주문 대사(reconciliation) 적용 검증

## 범위

- `upbit_live_reconciliation_applications` DB 계약
- live `reconciliation_runs(status='succeeded')`의 application 동시 커밋 제약
- REST terminal snapshot과 live binding UUID·identifier 일치 검증
- 실제 REST 호출, 주문 제출, 주문 취소, private WebSocket 연결 미추가 확인

## 실행 결과

```text
uv run pytest tests/shared/test_upbit_live_reconciliation.py tests/contracts/test_p6_live_reconciliation_contract.py tests/e2e/test_live_postgres_p6_live_reconciliation.py -q
.....sss
5 passed, 3 skipped in 0.12s
```

```text
uv run ruff check .
All checks passed!
```

```text
uv run mypy apps/api apps/worker packages/shared tests
Success: no issues found in 182 source files
```

```text
uv run pytest -q
815 passed, 156 skipped, 1 warning in 63.58s (0:01:03)
```

```text
npm test && npm run build
Vitest: Test Files 29 passed (29), Tests 181 passed (181)
Vite build: built successfully
Known warning: Some chunks are larger than 500 kB after minification.
```

```text
GOODMONEYING_UPDATE_DB_SNAPSHOT=1 tests/e2e/run_dbmate_migration_e2e.sh
155 passed in 45.93s
dbmate 마이그레이션 E2E 통과: versions=28 data_rows=1 timezone=UTC API=200 snapshot=동일 집계상태=동일
```

## 확인한 안전 경계

- P6-9 live PostgreSQL E2E가 마이그레이션 E2E 목록에 포함됐다.
- live terminal REST snapshot은 binding UUID·identifier와 일치할 때만 원장에 적용된다.
- application insert는 `reconciliation_runs.evidence`의 `sourceEndpoint`, `orderUuid`, `identifier`, `state`, `canResubmit=false`를 DB trigger로 다시 검증한다.
- live succeeded reconciliation run은 같은 transaction 안의 `upbit_live_reconciliation_applications` 없이는 커밋되지 않는다.
- adapter와 store 변경 파일에 `requests`, `httpx`, `aiohttp`, `urlopen`, `.post(`, `.delete(`, `websockets.connect` 사용이 없다.
- 코드 리뷰 에이전트 재검토 결과 Critical/Important/Minor 없음, Ready to merge.

## 남은 제약

- 실제 submit worker는 아직 구현하지 않는다.
- 실제 Upbit REST client와 private WebSocket 연결은 이번 범위가 아니다.
