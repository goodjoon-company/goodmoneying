# Task

이 디렉터리는 과거 repo-local Task 원문을 Milestone별로 요약한 색인만 둔다.

## 운영 방식

- 새 실행 단위는 GitHub Issue로 관리한다.
- `docs/Task/`에는 `M0.md`, `M1.md`, `M2.md`, `M3.md`처럼 Milestone별 요약 문서만 둔다.
- 긴 실행 계획, 체크박스, 진행 로그는 GitHub Issue 또는 PR(Pull Request) 댓글에 남긴다.
- 검증 증적은 `docs/Test/`, 오래 유지되는 변경 요약은 `docs/History/`에 남긴다.

## 현재 요약

- `M0.md`: 제품/문서/단일 기준 정비
- `M1.md`: 업비트 수집 운영 MVP와 배포 기반
- `M2.md`: 데이터 수집관리, Backfill 관리, 관심종목
- `M3.md`: 운영 콘솔 고도화, realtime/backfill 세부 운영, 체결 히트맵
- `P1.md`: 시스템 트레이딩 데이터 기반, 자동 정책·백필·구독·품질
- `P2.md`: 연구 데이터 계층, 불변 집계·지표·미시구조·데이터셋 버전·Data Lab

## 과거 상세 Task 처리

- 2026-07-10 GitHub Issue #5에서 기존 `*-T*.md` 상세 Task 10개를 삭제 후보로 확정하고 제거했다.
- 완료 결과는 `M1.md`~`M3.md`, 대응하는 `docs/Test/`, `docs/History/`에 보존한다.
- 상세 문서에 남아 있던 미완료 체크박스는 실제 미실행 작업이 아니라 완료 뒤 갱신되지 않은 과거 계획 상태였으므로 새 실행 단위로 이관하지 않는다.
- 이후 실행 상태와 체크리스트는 GitHub Issue를 단일 기준(source of truth)으로 사용한다.
