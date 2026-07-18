# DB 계약 개발 사양

이 디렉터리는 저장소가 강제하는 테이블, 컬럼, 제약조건, 인덱스의 단일 기준(source of truth)이다. 제품 요구사항이나 API 필드를 이 문서에 복제하지 않고, DB 변경 이력은 `migrations/`에서 확인한다.

## 기준 파일과 소비자

| 파일 | 정의 | 소비자 |
|---|---|---|
| `migrations/*.sql` | 순서와 데이터 변환을 포함한 버전 DB 변경의 단일 기준(source of truth) | dbmate, 배포 절차, 계약 테스트 |
| `schema.sql` | dbmate가 현재 DB에서 생성한 검토용 스키마 스냅샷(schema snapshot) | 코드 리뷰, 빈 DB 검증 |

SQLite는 격리 테스트를 위한 저장소 구현이다. PostgreSQL과 동일한 도메인 제약을 유지해야 하지만, PostgreSQL DDL의 대체 단일 기준은 아니다.

## 기록 기준

- 테이블, 컬럼, 제약조건, 인덱스, view, trigger 등 DB가 강제하는 정의를 기록한다.
- Architecture 문서에는 schema 상세를 복사하지 않고 이 위치를 링크한다.

## 적용 기준

- DB 스키마 변경은 새 `migrations/*.sql` 파일로만 작성한다. 이미 공유되거나 적용된 마이그레이션(migration)은 수정하지 않는다.
- `schema.sql`은 직접 수정하지 않는다. 마이그레이션 적용 후 dbmate의 `dump` 명령으로 다시 생성한다.
- `20260717000900_p2_candle_rollup_lineage.sql`은 최신 원천 캔들 투영, 추가 전용 개정 원장, 계산 버전별 집계 계보와 런타임 최소 권한을 추가하는 forward-only 계약이다.
- `20260717001200_p2_microstructure.sql`은 receipt와 정규화 호가·체결 원천의 직접 계보, 1분 미시구조 정의·추가 전용 물질화·통계, 더티 범위 무효화와 런타임 최소 권한을 추가하는 forward-only 계약이다.
- `20260718000100_p3_strategy_versions.sql`은 전략 정의, 전략 graph, 불변 strategy version, parameter의 append-only 저장 경계를 추가하는 forward-only 계약이다.
- `20260718000200_p4_backtest_runs.sql`은 published 전략 버전과 sealed 데이터셋 버전을 입력으로 하는 백테스트 run, trade, equity, metric, artifact의 영속화 경계와 terminal 결과 봉인을 추가하는 forward-only 계약이다.
- `20260718000300_p4_backtest_worker_leases.sql`은 백테스트 run의 worker 임대(lease), generation fencing, 재시도 대기(`retry_wait`), dead-letter와 claim 인덱스를 추가하는 forward-only 계약이다.
- `20260718000500_p5_portfolio_bot_risk.sql`은 P5 paper/shadow 실행을 위한 portfolio, policy, capital allocation, bot instance, order intent, simulated exchange order, fill, position projection, risk limit/event, kill switch 영속화 경계를 추가하는 forward-only 계약이다.
- `20260718000600_p5_portfolio_api_commands.sql`은 API로 생성된 portfolio의 명령 증거와 멱등 키 부분 고유 인덱스를 추가하는 forward-only 계약이다.
- `20260718000700_p5_paper_execution_jobs.sql`은 approved paper 주문 의도 실행을 위한 임대·재시도·dead-letter queue를 추가하는 forward-only 계약이다.
- `schema.sql`의 PostgreSQL·pg_dump 버전 머리글은 환경 차이로 인한 잡음을 막기 위해 정규화하며, CI의 PostgreSQL 17.10 E2E가 기준선 결과와 파일 차이를 검사한다.
- API와 워커의 런타임 저장소는 스키마를 생성하거나 변경하지 않는다. 개발 시작과 배포 전에 dbmate를 별도 단계로 실행한다.
- 연결 역할은 마이그레이션이 요구하는 DDL(Data Definition Language)과 데이터 변경 권한을 가져야 한다. 현재 기준선 migration이 `current_database()`의 KST 기본값을 변경하므로 DB 소유자(database owner) 또는 superuser 권한이 필요하다. 이 KST 기본값은 ADR-0013에 따라 신규 migration에서 UTC로 전환한다. 절대 시각과 기존 행을 보존하는 이행 검증 전에는 운영 DB에 적용하지 않는다.
- 개발 환경의 명령과 작성 절차는 프로젝트의 `dev.sh` 사용법 문서를 따른다.
- 원천 데이터, 화면용 뷰 모델, 작업·heartbeat, 감사 기록의 책임 분리는 [아키텍처 개발 사양](../../02_Architecture.md)을 따른다.
