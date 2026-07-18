# 2026-07-18 P6-2 order-test 증적과 live 주문 identifier 인계

## 변경 요약

- `20260718000900_p6_order_identity_separation.sql` migration을 추가했다.
- `exchange_accounts`, `upbit_order_identifier_reservations`, `live_order_identifiers`, `upbit_order_test_runs` DB 계약을 추가했다.
- `docs/contracts/db/schema.sql`을 dbmate dump로 갱신했다.
- `derive_upbit_live_order_identifier()`와 `is_upbit_live_order_identifier()` shared utility를 추가했다.
- `live_order_identifiers` trigger가 `order_intents.idempotency_key`와 결정론적 identifier를 재검산하고, order-test 응답 UUID·identifier와 live identifier의 양방향 재사용을 거부하도록 보강했다.
- `upbit_order_identifier_reservations` registry와 unique key가 live/test 동시 삽입 경쟁에서도 같은 계좌의 같은 식별자 재사용을 원자적으로 차단한다.
- `upbit_order_test_runs`는 UPDATE·DELETE를 거부하는 append-only 증적으로 고정했다.
- shared utility는 계좌 안정 식별자와 멱등 키의 앞뒤 공백을 거부한다.
- live PostgreSQL E2E를 migration E2E 스크립트에 연결했다.
- Product, Architecture, DB README, P6 Task 문서를 P6-2 상태로 현행화했다.

## 안전 경계

- `upbit_order_test_runs`의 `lookup_allowed`와 `cancel_allowed`는 항상 false다.
- `live_order_identifiers.identifier`는 `^gm1_[a-z2-7]{52}$` 패턴만 허용한다.
- live identifier는 같은 `order_intents.idempotency_key`에서 계산한 값만 저장할 수 있다.
- order-test 응답 UUID·identifier는 같은 계좌의 live identifier로 재사용할 수 없다.
- live identifier와 order-test 응답 식별자는 같은 계좌 namespace registry에서 동시에 예약될 수 없다.
- order-test 증적은 append-only이며 UPDATE·DELETE할 수 없다.
- 실제 주문 제출, 취소, private WebSocket, 출금 권한 관련 변경은 없다.

## 검증

- 상세 증적: `docs/Test/2026-07-18-P6-2-order-identity-검증.md`
- RED: `uv run pytest tests/contracts/test_p6_order_identity_contract.py tests/shared/test_live_order_identity.py -q` → 모듈·migration 부재로 수집 실패
- GREEN: 같은 명령 → `4 passed`
- Targeted: `uv run pytest tests/contracts/test_p6_order_identity_contract.py tests/shared/test_live_order_identity.py tests/scripts/test_migration_e2e_script.py tests/e2e/test_live_postgres_p6_order_identity.py -q` → `5 passed, 1 skipped`
- Ruff: 관련 파일 `All checks passed!`
- Mypy: `uv run mypy packages/shared tests/shared tests/contracts tests/scripts` → `Success: no issues found in 82 source files`
- DB migration E2E: `GOODMONEYING_UPDATE_DB_SNAPSHOT=1 tests/e2e/run_dbmate_migration_e2e.sh` → `versions=23 data_rows=1 timezone=UTC API=200 snapshot=동일 집계상태=동일`, live PostgreSQL `136 passed`
- 코드 리뷰 반영 후 DB migration E2E: 같은 명령 → `versions=23 data_rows=1 timezone=UTC API=200 snapshot=동일 집계상태=동일`, live PostgreSQL `136 passed in 36.92s`
- registry 동시성 보강 후 DB migration E2E: 같은 명령 → `versions=23 data_rows=1 timezone=UTC API=200 snapshot=동일 집계상태=동일`, live PostgreSQL `137 passed in 36.70s`
- append-only 보강 후 DB migration E2E: 같은 명령 → `versions=23 data_rows=1 timezone=UTC API=200 snapshot=동일 집계상태=동일`, live PostgreSQL `137 passed in 37.07s`

## 후속 범위

- `live_disabled` capability gate
- private `myOrder` 무이벤트 정상 처리와 REST 대사
- 안전 주문 adapter outbox와 권한 readiness
