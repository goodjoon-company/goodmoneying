# 업비트 외부 API 계약

[`upbit-api-catalog.yaml`](upbit-api-catalog.yaml)은 업비트 개발자 센터 v1.6.3의 `llms.txt`와 개별 공식 마크다운(markdown)을 2026-07-16에 확인한 REST·WebSocket 기능 카탈로그다. 기능 식별자, 메서드·경로, 파라미터 타입·필수 여부, 요청 제한 그룹, 비파괴 안전 등급의 단일 기준(source of truth)이다.

공식 문서가 변경되면 이 계약과 자동 계약 테스트를 먼저 갱신한다. `blocked` 항목은 화면 표시와 로컬 요청 미리보기만 허용하며 어떤 환경에서도 업비트 상향 전송 대상으로 해석하지 않는다.
