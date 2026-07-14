# DB 계약 개발 사양

이 디렉터리는 저장소가 강제하는 테이블, 컬럼, 제약조건, 인덱스의 단일 기준(source of truth)이다. 제품 요구사항이나 API 필드를 이 문서에 복제하지 않고, 정확한 정의는 `schema.sql`에서 확인한다.

## 기준 파일과 소비자

| 파일 | 정의 | 소비자 |
|---|---|---|
| `schema.sql` | 현재 기준 PostgreSQL DDL(Data Definition Language) | PostgreSQL 저장소, API, 워커, 계약 테스트 |
| migration 파일 또는 디렉터리 | 데이터 변환·순서 보장이 필요한 변경 | 배포·운영 절차 |

SQLite는 격리 테스트를 위한 저장소 구현이다. PostgreSQL과 동일한 도메인 제약을 유지해야 하지만, PostgreSQL DDL의 대체 단일 기준은 아니다.

## 기록 기준

- 테이블, 컬럼, 제약조건, 인덱스, view, trigger 등 DB가 강제하는 정의를 기록한다.
- Architecture 문서에는 schema 상세를 복사하지 않고 이 위치를 링크한다.

## 적용 기준

- `schema.sql`은 개발 환경에서 반복 적용해도 실패하지 않는 idempotent DDL(Data Definition Language)로 유지한다.
- 운영 서버(Operations Server)는 시작 시 `schema.sql`을 적용해 기존 개발 DB에 새 테이블 또는 인덱스가 추가된 경우에도 누락 schema를 보강한다.
- 기존 테이블의 컬럼 변경, 제약조건 변경, 데이터 변환이 필요한 경우에는 별도 migration 작업과 검증 증적을 추가한다.
- 원천 데이터, 화면용 뷰 모델, 작업·heartbeat, 감사 기록의 책임 분리는 [아키텍처 개발 사양](../../02_Architecture.md)을 따른다.
