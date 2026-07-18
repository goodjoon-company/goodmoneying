# 2026-07-18 P6-3 live capability gate 인계

## 변경 요약

- `20260718001000_p6_live_capability_gate.sql` migration을 추가했다.
- `trading_capabilities` DB 계약으로 global live capability 권위 로그를 append-only로 저장한다.
- `reject_p6_trading_capability_mutation()` trigger가 증적 UPDATE·DELETE를 거부한다.
- `LiveCapabilityRecord`, `evaluate_live_capability()`, `evaluate_live_capability_fail_closed()`, `fetch_global_live_capability_record()` shared utility를 추가했다.
- live PostgreSQL E2E를 migration E2E 스크립트에 연결했다.
- 코드 리뷰 대비 검증 보강으로 명시 `live_disabled`, `ci:`·`ai:`·`service:` actor 차단, append-only DELETE 거부를 직접 검증했다.
- Product, Architecture, DB README, P6 Task 문서를 P6-3 상태로 현행화했다.

## 안전 경계

- 권위 행 없음, DB 조회 실패, 배포 SHA 불일치, 승인 만료, 명시 비활성은 모두 `live_disabled`다.
- `ci:`, `ai:`, `service:` actor는 DB 제약으로 capability 기록을 만들 수 없다.
- 실제 주문 제출, 취소, private WebSocket, 출금 권한, live 활성화 API 관련 변경은 없다.

## 검증

- 상세 증적: `docs/Test/2026-07-18-P6-3-live-capability-검증.md`
- RED: `uv run pytest tests/contracts/test_p6_live_capability_contract.py tests/shared/test_live_capability.py tests/e2e/test_live_postgres_p6_live_capability.py -q` → 모듈·migration 부재로 수집 실패
- GREEN: 같은 명령 → 1차 `5 passed, 1 skipped`, 검증 보강 후 `6 passed, 1 skipped`
- DB migration E2E: `GOODMONEYING_UPDATE_DB_SNAPSHOT=1 tests/e2e/run_dbmate_migration_e2e.sh` → `versions=24 data_rows=1 timezone=UTC API=200 snapshot=동일 집계상태=동일`, live PostgreSQL `138 passed in 38.42s`

## 후속 범위

- private `myOrder` 무이벤트 정상 처리와 REST 대사
- 안전 주문 adapter, outbox, 출금 권한 미사용 readiness 검증
