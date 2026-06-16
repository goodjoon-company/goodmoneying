# DB 계약

DB schema의 source of truth를 둔다.

## 권장 파일

- `schema.sql`: 현재 기준 schema
- migration 파일 또는 migration 디렉터리 링크: 실제 적용 순서가 중요한 경우

## 기록 기준

- 테이블, 컬럼, 제약조건, 인덱스, view, trigger 등 DB가 강제하는 정의를 기록한다.
- Architecture 문서에는 schema 상세를 복사하지 않고 이 위치를 링크한다.
