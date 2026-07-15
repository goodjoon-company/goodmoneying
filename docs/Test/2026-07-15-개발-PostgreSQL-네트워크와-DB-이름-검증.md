# 개발 PostgreSQL 네트워크와 DB 이름 검증

Status: Complete
Date: 2026-07-15
Related Task: 사용자 요청에 따른 개발 환경 긴급 장애 조치(별도 GitHub Issue 미생성)
Environment: macOS 로컬 앱, Tailscale `100.66.66.104`, 원격 Docker Compose PostgreSQL

## 검증 목표

- 원격 PostgreSQL 공개 포트가 Tailscale 주소에만 바인딩(binding)되는지 확인한다.
- Docker 브리지 네트워크(bridge network)의 서브넷(subnet)과 게이트웨이(gateway)를 고정하고 PostgreSQL 접근 제어 규칙이 정확한 게이트웨이 `/32`만 허용하는지 확인한다.
- `.env`의 `GOODMONEYING_DATABASE_URL`에 지정된 데이터베이스 이름을 스키마(schema) 초기화가 그대로 사용하는지 확인한다.
- 로컬 앱이 `.env`의 원격 PostgreSQL로 접속해 상태 확인과 대시보드 API를 제공하는지 확인한다.

## 원격 적용값과 백업

| 항목 | 적용값 |
|---|---|
| Docker Compose 위치 | `/home/goodjoon/infra/postgresql/docker-compose.yml` |
| PostgreSQL 접근 제어 위치 | `/home/goodjoon/infra/postgresql/config/pg_hba.conf` |
| 포트 바인딩 | `100.66.66.104:5432:5432` |
| 고정 서브넷 | `172.30.10.0/24` |
| 고정 게이트웨이 | `172.30.10.1` |
| PostgreSQL 허용 규칙 | `host all all 172.30.10.1/32 scram-sha-256` |
| 변경 전 백업 | `/home/goodjoon/infra/postgresql/backups/20260715-005240` |

- 백업에는 Compose, `pg_hba.conf`, `goodmoneying-dev` 논리 덤프(logical dump), 전역 역할(global role), SHA-256 체크섬(checksum)이 있으며 모든 체크섬이 일치했다.
- 데이터 볼륨(volume)은 외부 볼륨 `postgresql_data`를 유지했고 컨테이너 재생성 시 `down -v`를 사용하지 않았다.
- 롤백(rollback)은 백업의 Compose와 `pg_hba.conf`를 복원한 뒤 같은 디렉터리에서 `docker compose down && docker compose up -d`로 수행한다.

## TDD 증적

| 조각 | RED | GREEN |
|---|---|---|
| Tailscale 전용 바인딩 | Compose에 `100.66.66.104:5432:5432`가 없어 검사 종료 코드 `1` | 컨테이너 공개 포트가 `100.66.66.104:5432->5432/tcp`로 표시 |
| 고정 Docker 네트워크 | 고정 서브넷과 게이트웨이가 없어 각 검사 종료 코드 `1` | 컨테이너 주소 `172.30.10.2`, 게이트웨이 `172.30.10.1` 확인 |
| PostgreSQL 접근 제어 | `172.30.10.1/32` 규칙이 없어 검사 종료 코드 `1` | 정확한 `/32` 규칙 존재, `pg_hba_file_rules` 파싱 오류 `0`건 |
| 현재 DB 이름 적용 | 계약 테스트가 `current_database()` 부재로 `1 failed` | 동적 `ALTER DATABASE` 계약으로 대상 테스트 `1 passed` |
| DB 권한 전제 | 계약 문서에 영구 DB 설정에 필요한 소유권 전제가 없어 대상 테스트 `1 failed` | `current_database()`의 DB 소유자(database owner) 요구를 단일 기준에 기록하고 대상 테스트 통과 |
| 실제 앱 연결 | 변경 전 `no pg_hba.conf entry for host \"172.18.0.1\"`; 네트워크 수정 후 하드코딩된 `goodmoneying` DB 때문에 `InvalidCatalogName` | `.env`가 지정한 `goodmoneying-dev`에 스키마를 적용하고 API 상태 확인과 대시보드 조회 성공 |
| 시스템 관리 E2E | 격리 서버가 워커·집계 상태를 시드하지 않아 시스템 목록이 비어 단독 재현도 `1 failed` | E2E 전용 저장소에 하트비트와 최소 캔들·집계 작업을 준비한 뒤 단독 시나리오 `1 passed` |

## 자동화 검증

| 명령 | 결과 |
|---|---|
| `uv run pytest tests/contracts/test_timezone_contract.py -q` | Pass, `3 passed` |
| `GOODMONEYING_DATABASE_URL=postgresql://invalid.invalid/should-not-connect uv run pytest tests/e2e/test_live_postgres_database_url.py -q` | Pass, 라이브 플래그가 없으면 가져오기 부작용 없이 `1 skipped` |
| `.env` 로드 후 `GOODMONEYING_LIVE_POSTGRES_TEST=1 uv run pytest tests/e2e/test_live_postgres_database_url.py -q` | Pass, 실제 PostgreSQL E2E `1 passed` |
| `uv run mypy apps/api apps/worker packages/shared tests` | Pass, 43개 소스 파일(source file) 오류 0건 |
| `uv run ruff check .` | Pass |
| `uv run pytest` | Pass, `163 passed`, 실제 DB 테스트 `1 skipped`, Starlette 사용 중단 예정(deprecation) 경고 1건 |
| `npm test` | Pass, 테스트 파일 9개와 테스트 48개 |
| `npm run build` | Pass, TypeScript와 Vite 프로덕션 빌드 성공 |
| `npm run e2e` | Pass, Chromium 시나리오 5개와 시험 서버 종료 검증 성공 |
| `git diff --check` | Pass, 공백 오류 없음 |

## 실제 환경 검증

- 다음 명령은 비밀값을 출력하지 않고 원격 설정과 연결 경계를 재검증한다.

```bash
ssh goodjoon@100.66.66.104 \
  "cd /home/goodjoon/infra/postgresql && \
   grep -nE '100\\.66\\.66\\.104:5432:5432|subnet: 172\\.30\\.10\\.0/24|gateway: 172\\.30\\.10\\.1' docker-compose.yml && \
   grep -nE '^[[:space:]]*host[[:space:]]' config/pg_hba.conf"
ssh -t goodjoon@100.66.66.104 \
  "sudo docker inspect --format '{{.State.Health.Status}}|{{range .NetworkSettings.Networks}}{{.IPAddress}}|{{.Gateway}}{{end}}' postgresql-postgresql-1 && \
   sudo docker port postgresql-postgresql-1 5432"
nc -vz -G 3 100.66.66.104 5432
nc -vz -G 3 192.168.55.40 5432
```

- 설정 출력은 Compose 18행의 `100.66.66.104:5432:5432`, 42행의 `172.30.10.0/24`, 43행의 `172.30.10.1`, HBA 9행의 `172.30.10.1/32 scram-sha-256`였다.
- HBA 호스트 규칙 전체를 함께 확인했다. Docker 고정 서브넷과 겹치는 허용 범위는 게이트웨이 `/32` 하나다. 기존 루프백 `127.0.0.1/32`, `::1/128`과 LAN `192.168.0.0/16`, `10.0.0.0/8` 규칙은 유지했다. 호스트 포트가 Tailscale 주소에만 바인딩되어 LAN 주소로는 공개되지 않는다.
- 적용 직후 `docker inspect` 출력은 `healthy|172.30.10.2|172.30.10.1`, `docker port` 출력은 `100.66.66.104:5432`였다.
- macOS에서 `nc`로 재검증한 결과 Tailscale `100.66.66.104:5432` 연결은 성공하고 LAN `192.168.55.40:5432` 연결은 `Connection refused`로 실패했다.
- 로컬에서 `./dev.sh app start api`를 실행했으며 `.env`의 호스트 `100.66.66.104`, 포트 `5432`, 데이터베이스 `goodmoneying-dev`를 사용했다. 인증 정보는 증적에 기록하지 않았다.
- `GET /health`는 `{"status":"ok"}`를 반환했다.
- `GET /v1/dashboard/summary`가 성공했고 응답 크기는 19,690바이트였다.
- `.env`를 비밀값 없이 읽는 Python/psycopg 검사로 `current_database`, 현재 역할, DB 소유자, 세션 시간대, `pg_db_role_setting`, 공개 테이블 수를 조회했다. 출력은 `goodmoneying-dev|goodmoneying|goodmoneying|Asia/Seoul|TimeZone=Asia/Seoul|25`였다. 따라서 연결 역할의 DB 소유권과 영구 기본 시간대 설정을 함께 확인했다.
- 검증 후 로컬 API를 종료해 시험 프로세스를 남기지 않았다.
- 실제 PostgreSQL E2E는 비기본 DB 이름을 요구하고 `PostgresOperationsRepository` 초기화, `/health`, `/v1/dashboard/summary`, 연결 DB 일치, DB 소유권, 옵션 없는 새 연결의 KST 기본값, 영구 DB 설정과 테이블 생성을 자동 단언(assertion)한다.
- 라이브 플래그를 검사한 뒤에만 API 모듈을 가져오므로, 셸에 DB URL만 있는 기본 테스트 실행은 외부 DB에 접속하거나 스키마를 적용하지 않는다.

## 계약과 문서 영향

- DB 계약의 KST 시간대 정책은 유지한다.
- 데이터베이스 이름을 `goodmoneying`으로 고정하지 않고 연결 URL이 선택한 `current_database()`에 정책을 적용한다.
- 런타임 역할은 선택한 DB의 소유자여야 한다. 이는 `ALTER DATABASE ... SET`을 실행해 KST 기본값을 영구 적용하기 위한 명시적 계약 전제다.
- API, 메시지(message), 제품 범위와 모듈 경계는 변경하지 않았다.
- 기존 PostgreSQL 구조나 장기 아키텍처 선택을 바꾸지 않는 개발 환경 보안·운영 보정이므로 새 ADR(Architecture Decision Record)은 필요하지 않다고 판정했다.
- 이번 작업은 사용자가 진행 중 장애의 즉시 조치를 직접 요청한 긴급 실행 단위여서 별도 GitHub Issue를 만들지 않았다. 요구사항, 리뷰와 결과는 이 검증 증적과 History에 연결했다.

## 코드 리뷰

- 최초 독립 리뷰의 중요(Important) 3건인 실제 PostgreSQL 자동 검증 부재, DB 소유권 전제 누락, 원격 네트워크·영구 시간대 증적 부족을 모두 보완했다.
- 재리뷰에서 발견된 라이브 플래그 검사 전 API 모듈 가져오기 부작용을 제거하고, 실제 환경 저장소 팩터리(environment repository factory) 배선을 검증하도록 수정했다.
- 전체 E2E 실패를 재현해 격리 서버의 워커 하트비트(heartbeat)·집계 상태 시드 누락을 원인으로 확인하고 테스트 전용 SQLite 상태만 보완했다.
- 최종 독립 재리뷰 결과 심각(Critical) 0건, 중요 0건, 경미(Minor) 2건으로 병합 가능 판정을 받았다.
- 경미 후속은 느린 CI에서 2분 하트비트가 만료될 가능성과 라이브 플래그를 켠 상태에서 운영 DB URL을 오지정할 가능성이다. 실제 DB E2E는 개발·테스트 DB에서만 실행해야 한다.

## 결론

개발 PostgreSQL은 고정된 Docker 게이트웨이를 통해서만 접근하고 호스트 포트는 Tailscale 주소에만 공개된다. 로컬 앱은 `.env`가 지정한 데이터베이스 이름과 접속 정보를 사용해 실제 API 요청까지 정상 처리한다.
