# Task

이 디렉터리는 repo-local 실행 단위 문서를 둔다. 외부 GitHub Issue, Jira, Linear가 있으면 그 ticket을 우선하고, 접근할 수 없거나 없는 경우 이 디렉터리의 Task 문서를 기준으로 진행한다.

## 파일 규칙

- 파일명: `M1-T01-YYYY-MM-DD-한글-태스크-제목.md`
- 문서 제목도 한글로 작성한다.
- 새 Task는 `docs/Task/템플릿.md`를 복사해 작성한다.

## 상태

- `Todo`: 아직 시작하지 않음
- `In Progress`: 진행 중
- `Ready for Verification`: 구현 완료, 검증 전
- `Done`: 검증 완료
- `Blocked`: 차단됨

## 규칙

- AI가 바로 실행할 수 있을 정도로 목표, 범위, 현재 맥락, 완료 기준, 검증 방법을 구체화한다.
- 진행 로그와 완료 요약은 Task 또는 외부 ticket comment에 남긴다.
- Product/Architecture 문서를 작업 로그 저장소로 쓰지 않는다.
