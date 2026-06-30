# 2026-06-30 Task 문서 GitHub 전환 검증

## 변경 범위

- `docs/Task/`의 상세 실행 문서를 Milestone별 요약 문서로 축약했다.
- 새 실행 단위는 GitHub Issue로 관리하도록 `AGENTS.md`, `docs/Task/README.md`, 제품 문서, 문서 색인을 갱신했다.
- 기존 상세 Task 링크는 Milestone 요약 문서 링크로 정리했다.

## 검증 명령

```bash
find docs/Task -maxdepth 1 -type f | sort
```

결과: `README.md`, `M0.md`, `M1.md`, `M2.md`, `M3.md`만 남는지 확인했다.

```bash
rg -n "docs/Task/M[0-9]-|docs/Task/템플릿|repo-local Task 문서" AGENTS.md docs --glob "!docs/Test/2026-06-30-Task-문서-GitHub-전환-검증.md"
```

결과: 출력 없음. 제거된 상세 Task 파일과 템플릿을 가리키는 현재 링크가 남지 않았다.

```bash
git diff --check
```

결과: 성공.

## 미검증 항목

- GitHub Issue 실제 생성/이관은 수행하지 않았다.
