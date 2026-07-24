# P7 dependency 보안 검증

- 일시: 2026-07-25 KST
- 대상 gate: `security.dependencies`
- 명령: `npm run p7:dependency-security`

## 증적

- `npm audit --audit-level=high --json`: high 0, critical 0, total 0
- 잠금 파일(lockfile): `package-lock.json`, `uv.lock` 존재
- CI 설치 경계: `npm ci`, `uv sync --frozen`
- 조치: `postcss` transitive 의존성을 취약 범위 `<=8.5.17` 밖으로 갱신하기 위해 `npm audit fix --package-lock-only`를 실행했다.

## 결과

통과. 외부 Node 의존성 audit과 재현 가능한 설치 경계를 P7 gate로 고정했다.
