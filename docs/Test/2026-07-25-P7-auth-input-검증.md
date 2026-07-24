# P7 auth-input 검증

- 일시: 2026-07-25 KST
- 대상 gate: `security.auth_input`
- 명령: `npm run p7:auth-input`

## 증적

실행 내부 명령:

```bash
uv run pytest tests/upbit_gateway/test_auth.py tests/upbit_gateway/test_websocket_security.py tests/api -q
```

결과:

- 105 passed
- 1 warning: FastAPI TestClient 경유 Starlette deprecation warning
- 인증 경계: 업비트 JWT HS512, query hash, 키 파일 권한, REST/WebSocket 운영자 토큰, Origin 검증
- 입력 경계: API 요청 본문과 주요 제품 REST 경계의 기존 테스트 묶음

## 결과

통과. 인증·입력 경계 회귀 테스트를 P7 security gate로 고정했다.
