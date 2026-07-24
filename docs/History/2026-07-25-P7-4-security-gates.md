# P7-4 security gates

## 변경

- P7 보안 gate 스크립트 `scripts/verify_p7_security_gates.py`를 추가했다.
- `security.dependencies`, `security.images`, `security.secrets`, `security.auth_input`을 `passed`로 전환했다.
- `postcss` high 취약점 해소를 위해 `package-lock.json`을 갱신했다.
- API, worker, Upbit gateway, web 런타임 Dockerfile을 비root 실행으로 변경했다.
- web Nginx 내부 listen/EXPOSE 포트를 8080으로 변경하고 이미지 내부 운영자 토큰 기본값을 제거했다.
- web Nginx 비root 런타임의 `/run/nginx.pid` 쓰기 권한을 보장했다.
- 로컬 compose와 prod-home web compose의 컨테이너 포트 매핑을 8080으로 맞췄다.

## 검증

- `uv run pytest tests/scripts/test_p7_security_gates.py -q`
- `uv run python scripts/verify_p7_security_gates.py dependencies`
- `uv run python scripts/verify_p7_security_gates.py images`
- `uv run python scripts/verify_p7_security_gates.py secrets`
- `uv run python scripts/verify_p7_security_gates.py auth-input`
- `docker build -f apps/api/Dockerfile -t goodmoneying-api:p7-security .`
- `docker build -f apps/worker/Dockerfile -t goodmoneying-worker:p7-security .`
- `docker build -f apps/upbit_gateway/Dockerfile -t goodmoneying-upbit-gateway:p7-security .`
- `docker build -f apps/web/Dockerfile -t goodmoneying-web:p7-security .`
- `docker build -f apps/migrations/Dockerfile -t goodmoneying-migrations:p7-security .`
- `docker run ... goodmoneying-web:p7-security`에 localhost upstream 환경 변수를 주입한 뒤 `docker exec id`와 HTTP 200 확인
- `uv run ruff check .` → 통과
- `uv run mypy apps/api apps/worker apps/upbit_gateway packages/shared tests` → 199개 source file 통과
- `uv run pytest -q` → 828 passed, 156 skipped, 1 warning
- `npm test` → 181 passed
- `npm run build` → 통과, 기존 Vite chunk size warning 유지
- `npm run e2e` → 27 passed, API·웹 시험 서버 종료 확인
- `tests/e2e/run_dbmate_migration_e2e.sh` → 155 passed, schema snapshot 동일
- `git diff --check` → 통과
- `uv run python scripts/verify_p7_quality_gates.py --mode release --manifest docs/contracts/quality/p7-quality-evidence.yaml` → 예상 실패: `resilience.load`, `resilience.soak`, `resilience.chaos`, `recovery.backup_restore`, `hygiene.unresolved_artifacts`

## 리뷰

- Critical: 없음
- Important: 없음
- Minor: 없음

비root 전환은 런타임 권한 축소를 위한 변경이다. web 외부 서비스 URL은 유지했지만 컨테이너 내부 포트가 80에서 8080으로 바뀌었으므로 배포 compose와 관련 테스트를 함께 갱신했다.
