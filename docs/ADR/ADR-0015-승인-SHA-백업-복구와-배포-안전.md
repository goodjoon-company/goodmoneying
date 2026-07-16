# ADR-0015: 승인 SHA, 백업·복구와 배포 안전

- 상태: Accepted
- 날짜: 2026-07-17
- 대체: ADR-0004의 보호 없는 `release` push 배포 결정

## 맥락

2026-07-17 현재 `main`과 `release`에는 branch protection이 없고 `prod` 환경 승인자도 없다. 최신 `main` CI는 Playwright 실패 상태이며 운영은 114 commit 이전 SHA다. 운영 PostgreSQL은 약 17GB지만 자동 백업·복원 rehearsal과 자동 rollback이 없다. `release` push가 즉시 자체 호스팅 runner 배포를 시작하므로 승인되지 않은 SHA와 부분 migration 위험을 기계적으로 차단해야 한다.

## 결정

- `main`은 전체 CI 성공을 required status check로 요구한다.
- `release`는 직접 push와 force push를 금지하고 승인 workflow만 같은 40자리 `main` SHA를 fast-forward한다.
- `prod` environment는 required reviewer와 release branch 제한을 적용한다.
- branch·environment protection, 성공 CI와 대상 SHA 동일성을 API로 증명하지 못하면 배포를 시작하지 않는다.
- 외부 GitHub Action은 commit SHA로 고정하고 workflow permission은 최소화한다.
- schema·data 변경 전에 장애 영역이 분리된 백업의 원본 DB·SHA, 완료 시각, 크기, checksum, 보관 기한, 복원 명령을 기록하고 최근 복원 rehearsal이 승인 RPO·RTO를 만족해야 한다.
- migration 실패는 app 전환 전에 중단한다. migration 후 부분 배포는 자동 down migration을 금지하고 schema 역호환이 증명된 전체 이전 image set 또는 승인 forward-fix로만 복구한다.
- 배포 성공은 모든 service·worker, heartbeat, row delta·freshness, 집계·백필, WebSocket, browser smoke, 오류 log·DB 용량, 서버별 image SHA와 global `live_disabled`를 검증한 뒤 기록한다.

## 대안

1. **현재 release push 유지**: 빠르지만 CI 실패·오입력·force push를 막지 못한다.
2. **migration 실패 시 자동 down**: 일부 migration과 데이터 변환은 되돌릴 수 없어 손상을 키울 수 있다.
3. **백업 존재 여부만 확인**: 복원 가능성과 RPO·RTO를 증명하지 못한다.

## 트레이드오프

- 승인과 백업·복원 검증으로 배포 시간이 늘어난다.
- GitHub·runner·운영 호스트 상태를 함께 확인하는 script가 필요하다.
- 대신 승인되지 않은 SHA, 복원 불가능한 migration, 혼합 version을 성공으로 오판하지 않는다.

## 결과와 후속 작업

- P0에서 자동 `release` push 트리거를 제거하고 승인 SHA 입력, 별도 Administration read 자격 증명의 GitHub API 증명, P8 exact-SHA 잠금을 이미지 build 전 실패 폐쇄형 gate로 적용했다. 현재 저장소 보호·`prod` 승인자·P8 enable SHA가 없으므로 이 gate는 의도대로 배포를 거부한다. Issue #34·#35가 백업·복원·전진 복구·확장 health·`live_disabled` 증적을 모두 마친 SHA만 enable 변수에 설정한다.
- Issue #34에서 backup·restore rehearsal, security와 failure injection을 검증한다.
- Issue #35에서 protection, 승인 SHA 승격, 확장 healthcheck와 운영 증적을 구현한다.
- gate가 완성되기 전에는 `release` 승격과 prod-home 배포를 수행하지 않는다.
