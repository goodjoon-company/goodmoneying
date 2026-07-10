# ADR-0006-M1-백필-배치-저장과-heartbeat-갱신

Status: Accepted
Date: 2026-06-21
Related Product: `docs/01_Product.md#요구사항`
Related Architecture: `docs/02_Architecture/upbit-collection-pipeline.md`
미래 결정 게이트 정제(Future Gate Refined By): `docs/ADR/ADR-0007-Post-MVP-아키텍처-결정-게이트.md`

## 맥락

백필 수집 워커(Backfill Collection Worker)는 업비트 1분봉을 200개 페이지(page) 단위로 조회하지만, 현재 구조는 결측 구간(Missing Range) 전체를 메모리에 모은 뒤 한 번에 DB upsert를 수행한다. 수개월 단위 결측 구간에서는 fetch가 오래 걸리는 동안 heartbeat와 진행 상태가 늦게 갱신되고, 운영 화면에서는 살아 있는 워커가 지연으로 보일 수 있다.

최근 운영 확인에서는 대량 upsert 자체보다 업비트 fetch 반복 구간이 더 긴 지연 원인으로 확인됐다. 따라서 워커 생존 신호와 저장 완료 진행률을 같은 시점에 묶으면 운영 판단이 흐려진다.

## 결정

백필 수집은 fetch 성공, DB batch upsert 완료, target 진행 상태 갱신을 분리한다.

- 백필 워커는 목표 기간 전체를 바로 요청하지 않고, DB에 이미 저장된 분(minute)을 제외한 결측 구간만 업비트에 요청한다.
- 업비트 fetch page는 업비트 API 제한에 맞춰 200개 단위를 유지한다.
- DB 저장 배치(batch)는 기본 최대 3000개 row로 제한한다.
- 기본 배치 크기는 외부 설정인 `GOODMONEYING_BACKFILL_BATCH_SIZE`로 변경할 수 있다.
- fetch가 성공하면 아직 DB 저장 전이어도 `backfill_collection` heartbeat를 갱신한다.
- `rows_written_count`와 `last_completed_at`은 해당 DB batch upsert가 성공한 뒤에만 갱신한다.
- 실패(failed)한 백필 작업은 재개(Resume)를 허용한다. 재개 시 기존 저장 데이터를 삭제하지 않고 결측 구간을 다시 계산해 없는 구간만 업비트에 요청한다.
- 워커 로그 레벨은 `GOODMONEYING_LOG_LEVEL`로 설정한다. 기본값은 `INFO`이며, `DEBUG`에서는 백필 job claim, target 시작, 결측 범위 계산, fetch 성공, DB batch upsert 경계를 남긴다.
- 백필 작업과 대상 상태의 단일 기준(Source Of Truth)은 계속 `backfill_jobs`와 `backfill_job_targets`다.
- 백필 동시성(Concurrency)은 1로 유지한다. 코인별 병렬 백필, 분산 rate limiter, 메시지 큐(Message Queue)는 처리량 또는 rate limit 문제가 측정될 때 `ADR-0007`의 결정 게이트를 적용한다.

## 대안

| 대안 | 장점 | 단점 |
|---|---|---|
| 결측 구간 전체 fetch 후 한 번에 upsert 유지 | 구현이 단순하고 기존 테스트 변경이 적다 | 긴 fetch 동안 heartbeat와 진행률이 늦게 갱신되고 메모리 사용량이 커진다 |
| 업비트 200개 page마다 즉시 upsert | heartbeat와 진행률이 가장 자주 갱신된다 | DB round-trip이 많아지고 작은 batch가 많아질 수 있다 |
| fetch page는 200개로 유지하고 DB batch는 최대 3000개로 묶기 | API 제약과 DB 효율을 함께 맞추고 운영 조정 여지를 둔다 | fetch stream과 DB batch 누적 경계 테스트가 필요하다 |

## 결과

- 백필 워커는 장시간 작업 중에도 fetch 성공 지점마다 heartbeat를 갱신할 수 있다.
- 운영 화면의 `rows_written`과 `last_completed_at`은 DB에 실제 반영된 상태만 표시한다.
- 이미 저장된 캔들 구간은 재요청하지 않으므로 안전 재시작(Safe Restart)과 일반 백필 모두 중복 API 요청을 줄인다.
- 실패 후 재개도 같은 결측 구간 계산을 사용하므로 이미 들어간 데이터는 업비트에 다시 요청하지 않는다.
- 운영 배포 후에도 `GOODMONEYING_LOG_LEVEL=DEBUG`로 올리면 워커 재기동 없이 컨테이너 실행 설정 기준의 상세 로그를 남길 수 있다.
- DB schema와 OpenAPI 응답 shape는 변경하지 않는다. 진행 상태는 기존 `backfill_job_targets.rows_written_count`, `last_completed_at`, `processed_missing_range_count`, `estimated_missing_range_count`를 사용한다.

## 후속 작업

- `GOODMONEYING_BACKFILL_BATCH_SIZE` 파싱, 기본값, 검증 오류를 테스트로 고정한다.
- fetch streaming API와 DB batch upsert 경계를 worker 테스트로 고정한다.
- 일시정지(Pause), 중지(Stop), 이어서하기(Resume), 안전 재시작 동작이 batch 경계에서도 유지되는지 검증한다.
- 실패 작업 재개 버튼과 재개 시 결측 구간만 요청하는 동작을 UI, repository, worker 테스트로 고정한다.
- 운영 워커의 로그 레벨 설정과 디버그 로그 이벤트를 테스트로 고정한다.
