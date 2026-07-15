# 개발 PostgreSQL 네트워크와 DB 이름

Date: 2026-07-15
Related Task: 사용자 요청에 따른 개발 환경 긴급 장애 조치(별도 GitHub Issue 미생성)
Related Verification: [개발 PostgreSQL 네트워크와 DB 이름 검증](../Test/2026-07-15-개발-PostgreSQL-네트워크와-DB-이름-검증.md)

## 변경 요약

- 원격 개발 PostgreSQL의 Docker Compose 브리지 네트워크(bridge network)를 `172.30.10.0/24`, 게이트웨이(gateway)를 `172.30.10.1`로 고정했다.
- PostgreSQL 호스트 포트 `5432`는 Tailscale 주소 `100.66.66.104`에만 바인딩(binding)했다.
- `pg_hba.conf`는 Docker 프록시(proxy)의 고정 서브넷에서 게이트웨이 `172.30.10.1/32`만 SCRAM 인증으로 추가 허용한다. 기존 루프백과 LAN 규칙은 유지했다.
- DB 계약의 하드코딩된 `goodmoneying` 이름을 제거하고 연결 URL이 선택한 현재 데이터베이스에 KST 시간대 설정을 적용한다.

## 영향 문서와 계약

- 단일 기준(source of truth): `docs/contracts/db/schema.sql`
- 런타임 역할은 선택한 DB의 소유자(database owner)여야 하며, 이 전제는 `docs/contracts/db/README.md`에 기록했다.
- 원격 운영 파일: `/home/goodjoon/infra/postgresql/docker-compose.yml`, `/home/goodjoon/infra/postgresql/config/pg_hba.conf`
- 제품, API, 메시지(message), 모듈 아키텍처 계약은 변경하지 않았다.
- 기존 PostgreSQL 선택과 시스템 경계를 유지하는 국소적인 개발 환경 변경이므로 새 ADR(Architecture Decision Record)은 만들지 않았다.

## 검증

- 원격 컨테이너 상태, Tailscale 전용 포트 공개, 고정 컨테이너 주소와 게이트웨이, 접근 제어 규칙 파싱을 확인했다.
- 로컬 `.env`가 지정한 `goodmoneying-dev`에서 스키마 테이블 25개와 `Asia/Seoul` 시간대를 확인했다.
- API 상태 확인과 대시보드 요청, Python·웹 단위 테스트, 빌드, Playwright E2E, 정적 검사가 모두 통과했다.
- 선택 실행형 실제 PostgreSQL E2E가 비기본 DB 이름, API 요청, DB 소유권, 새 연결의 영구 KST 기본값을 자동 검증한다.
- 전체 E2E 재검증에서 드러난 격리 서버의 시스템 관리 상태 누락을 보완해 실시간 워커와 집계 진행률 시나리오를 결정적으로 준비한다.
- 상세 명령과 결과, 백업·롤백 경로는 연결된 검증 증적을 따른다.

## 리스크와 후속 작업

- Tailscale 주소가 바뀌면 Compose 포트 바인딩도 함께 갱신해야 한다.
- 고정 서브넷 `172.30.10.0/24`를 다른 Docker 프로젝트나 호스트 네트워크가 사용하게 되면 충돌 여부를 재확인해야 한다.
- HBA의 게이트웨이 규칙은 현재 `all all`이다. 전용 DB·역할이 장기 고정되면 `goodmoneying-dev`와 `goodmoneying`으로 범위를 좁히는 최소 권한 후속 검토가 필요하다.
- 실제 PostgreSQL E2E는 명시적 라이브 플래그를 사용하므로 개발·테스트 DB URL에서만 실행해야 한다. 운영 DB 오지정 방어와 느린 CI의 하트비트 만료 방지는 경미한 후속 개선 사항이다.
- 변경 전 백업은 `/home/goodjoon/infra/postgresql/backups/20260715-005240`에 보존했다.
