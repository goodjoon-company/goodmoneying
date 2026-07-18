# 2026-07-18 P6-3 live capability gate 검증

## 범위

- global live capability 권위 상태를 DB append-only 증적으로 고정한다.
- 권위 행 없음, 조회 실패, 배포 SHA 불일치, 승인 만료, 명시 비활성은 모두 `live_disabled`로 평가한다.
- CI·AI·service actor가 live capability를 변경하지 못하게 DB 제약으로 차단한다.

## RED

- `uv run pytest tests/contracts/test_p6_live_capability_contract.py tests/shared/test_live_capability.py tests/e2e/test_live_postgres_p6_live_capability.py -q`
  - 결과: 수집 오류
  - 원인: `goodmoneying_shared.live_capability` 모듈과 `20260718001000_p6_live_capability_gate.sql` migration이 없었다.

## GREEN

- `uv run pytest tests/contracts/test_p6_live_capability_contract.py tests/shared/test_live_capability.py tests/e2e/test_live_postgres_p6_live_capability.py -q`
  - 1차 결과: `5 passed, 1 skipped`
  - 코드 리뷰 대비 보강 후 결과: `6 passed, 1 skipped`
  - 보강 범위: 명시 `live_disabled`, `ci:`·`ai:`·`service:` actor 차단, append-only DELETE 거부
- `uv run ruff check packages/shared/goodmoneying_shared/live_capability.py tests/shared/test_live_capability.py tests/contracts/test_p6_live_capability_contract.py tests/e2e/test_live_postgres_p6_live_capability.py tests/scripts/test_migration_e2e_script.py`
  - 중간 결과: import 정렬 오류 1건
  - 수정 후 결과: `All checks passed!`
- 1차 `GOODMONEYING_UPDATE_DB_SNAPSHOT=1 tests/e2e/run_dbmate_migration_e2e.sh`
  - 결과: `1 failed, 137 passed`
  - 원인: E2E fixture가 새 계약의 `request_id`, `idempotency_key` 필수 컬럼을 누락했다.
- 수정 후 `GOODMONEYING_UPDATE_DB_SNAPSHOT=1 tests/e2e/run_dbmate_migration_e2e.sh`
  - 결과: `dbmate 마이그레이션 E2E 통과: versions=24 data_rows=1 timezone=UTC API=200 snapshot=동일 집계상태=동일`
  - 1차 live PostgreSQL: `138 passed in 37.03s`
  - 코드 리뷰 대비 보강 후 live PostgreSQL: `138 passed in 38.42s`

## 안전 경계

- 실제 Upbit 주문·취소·private WebSocket 호출 없음
- live 활성화 API 없음
- 환경변수 또는 API Key 존재만으로 `live_enabled`를 만들지 않음
