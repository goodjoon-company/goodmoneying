# API 계약 개발 사양

이 디렉터리는 브라우저와 운영 서버·업비트 API 게이트웨이 사이의 HTTP·WebSocket 인터페이스(interface) 단일 기준(source of truth)이다. 제품 의미는 [제품 개발 사양](../../01_Product.md), 런타임 전송 구조는 [아키텍처 개발 사양](../../02_Architecture.md)에서 확인하고, 경로·필드·오류 처리는 여기서만 정의한다.

## 기준 파일과 소비자

| 파일 | 정의 | 소비자 |
|---|---|---|
| `openapi.yaml` | FastAPI 운영 서버의 REST/HTTP 경로, 인증, 요청·응답·오류 모델 | `apps/web`, `apps/api`, API 계약 테스트 |
| `internal-realtime-stream.schema.json` | P2 내부 WebSocket envelope, sequence, cursor, heartbeat, snapshot_required 신호 | `useRealtimeAnalysis`, `realtimeStream.ts`, WebSocket 계약 테스트 |
| `realtime-analysis-websocket.schema.json` | 코인 분석 화면의 WebSocket 메시지 스키마 | `CoinAnalysis`, `analysis.py`, 메시지 테스트 |
| `realtime-analysis-websocket.md` | 분석 구독 순서, 메시지의 의미와 재연결 안내 | 분석 기능 개발자 |
| `realtime-system-management-websocket.md` | 시스템 관리 상태·진행률 메시지의 의미 | `SystemManagement`, 운영 서버 |
| `upbit-gateway.openapi.yaml` | 업비트 게이트웨이 health·catalog·endpoint_id 실행 경계 | 업비트 API 작업대, `apps/upbit_gateway` |
| `upbit-gateway-websocket.schema.json` | 연결·구독·프레임·오류 추적 이벤트 | 업비트 API 작업대, `apps/upbit_gateway` |

GraphQL을 도입하면 `schema.graphql`을 이 디렉터리에 추가하되, 별도 제품·아키텍처 결정 후 계약 테스트를 함께 추가한다.

## 기록 기준

- path, method, request, response, error model, auth requirement를 계약 파일에 기록한다.
- breaking change는 ADR 후보로 본다.
- OpenAPI가 WebSocket frame을 표현하지 않으므로 분석 WebSocket의 기계 검증 계약은 JSON Schema로 유지한다.
- SSE(Server-Sent Events)는 `openapi.yaml`의 HTTP 경로와 이벤트 이름 설명을 함께 유지한다. WebSocket 메시지를 OpenAPI의 임의 확장 필드로 중복 정의하지 않는다.
## 실시간 계약

- [코인 분석 웹소켓](realtime-analysis-websocket.md)
- [P2 내부 실시간 스트림 스키마](internal-realtime-stream.schema.json)
- [시스템 관리 웹소켓](realtime-system-management-websocket.md)
- [업비트 게이트웨이 OpenAPI](upbit-gateway.openapi.yaml)
- [업비트 게이트웨이 WebSocket 스키마](upbit-gateway-websocket.schema.json)
