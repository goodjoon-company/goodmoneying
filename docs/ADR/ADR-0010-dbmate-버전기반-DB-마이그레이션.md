# ADR-0010: dbmate 버전 기반 DB 마이그레이션

Date: 2026-07-15
Status: Accepted
Related Issue: [#16](https://github.com/goodjoon-company/goodmoneying/issues/16)

## 맥락

API와 워커는 PostgreSQL 저장소를 생성할 때마다 `docs/contracts/db/schema.sql` 전체를 실행한다. 이 방식은 반복 가능한 테이블·인덱스 추가에는 단순하지만, 적용 순서, DB별 적용 버전, 데이터 변환, 실패 복구와 운영 배포 게이트를 관리하지 못한다. 스키마 변경이 계속되면 이미 적용된 SQL 수정, 환경별 드리프트(drift), 앱 시작과 DDL(Data Definition Language) 권한 결합이 운영 위험이 된다.

## 결정

- DB 변경 이력은 dbmate `2.34.1`의 타임스탬프 버전 SQL을 사용한다.
- `docs/contracts/db/migrations/`를 DB 변경 이력의 단일 기준(source of truth)으로 둔다.
- `docs/contracts/db/schema.sql`은 dbmate와 `pg_dump`가 생성하는 현재 스키마 스냅샷(snapshot)이며 직접 수정하지 않는다.
- 최초 기준선(baseline)은 현재 멱등 스키마와 데이터 보정 SQL을 하나의 `migrate:up`으로 옮긴다. 기존 DB와 빈 DB 모두 같은 기준선 버전을 기록한다.
- 개발의 `./dev.sh app start`는 미적용 마이그레이션을 자동 적용한 뒤 앱을 시작한다. 명시적 `./dev.sh db migrate`는 적용 후 스키마 스냅샷도 갱신한다.
- API와 워커 런타임은 DDL을 실행하지 않는다.
- 운영은 배포 태그와 같은 태그의 전용 `goodmoneying-migrations` 이미지를 앱 서버에서 일회성으로 실행한다. 성공한 경우에만 API와 워커를 새 이미지로 갱신한다.
- 운영은 자동 `rollback`을 제공하지 않는다. 백업과 순방향 수정(forward fix)을 기본으로 하고, 개발에서만 안전한 `migrate:down`을 명시적으로 실행한다.
- 운영 변경은 이전 앱과 새 앱이 함께 동작할 수 있는 확장-축소(expand-contract) 순서를 따른다.

## 대안과 트레이드오프

| 대안 | 장점 | 단점 | 판단 |
|---|---|---|---|
| API·워커 시작 시 전체 `schema.sql` 실행 유지 | 별도 도구와 배포 단계가 없다. | 버전·순서·실패 이력이 없고 런타임이 DDL 권한을 가져야 한다. | 기각 |
| Alembic | Python 생태계와 풍부한 마이그레이션 API를 제공한다. | 현재 사용하지 않는 SQLAlchemy 계층을 도입한다. | 기각 |
| Flyway Community | 체크섬과 강한 이력 검증을 제공한다. | 현재 규모에는 실행·설정 복잡도가 크고 자동 `schema.sql` 덤프가 없다. | 보류 |
| dbmate 호스트 설치 | 구현이 빠르다. | 서버별 바이너리 버전과 SQL 복사 상태가 달라질 수 있다. | 기각 |
| dbmate 전용 불변 이미지 | 원시 SQL 구조를 유지하고 배포 태그와 변경 이력을 함께 고정한다. | 별도 이미지 빌드와 Compose 배포 단계가 필요하다. | 채택 |

## 결과

- 이후 적용된 마이그레이션 파일은 수정하지 않고 새 파일로 순방향 변경한다. dbmate는 버전만 기록하므로 CI와 리뷰가 불변성을 보완한다.
- 마이그레이션 역할은 스키마 변경 권한을 가져야 하지만, API·워커 역할은 장기적으로 데이터 접근 권한만 갖도록 분리할 수 있다.
- 마이그레이션 실패는 앱 시작과 배포를 중단한다. 운영 배포 전 기존 앱은 계속 실행된다.
- 큰 테이블 인덱스는 `CREATE INDEX CONCURRENTLY`와 `transaction:false` 등 PostgreSQL 잠금(lock) 영향을 별도 검토한다.
- `schema.sql` 생성에는 호환되는 `pg_dump`가 필요하며, 생성 실패나 Git 차이는 CI 실패로 처리한다.

## 후속 작업

- Issue #16에서 기준선, 개발 명령, CI, 전용 이미지, prod-home 배포 게이트와 실제 PostgreSQL E2E를 구현한다.
- 전용 마이그레이션 DB 역할 분리는 현재 운영 자격 증명과 배포가 안정된 뒤 후속 보안 작업으로 검토한다.
