# P7 backup/restore 검증

- 일시: 2026-07-25 KST
- 대상 gate: `recovery.backup_restore`
- 명령: `tests/e2e/run_dbmate_migration_e2e.sh`

## 증적

- migrations 이미지 build
- API 이미지 build
- 빈 DB 전체 적용
- 두 번째 멱등 적용
- schema snapshot 생성과 비교
- 기존 샘플 행 보존
- API smoke: HTTP 200
- 집계 상태 동일
- pytest 기반 migration E2E: 155 passed

## 결과

통과. dbmate migration 체인이 빈 DB와 기존 DB 양쪽에서 재현 가능하며 schema snapshot이 동일함을 확인했다.
