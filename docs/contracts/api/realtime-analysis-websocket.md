# 코인 분석 WebSocket 계약

Related Schema: `realtime-analysis-websocket.schema.json`
Related ADR: `docs/ADR/ADR-0008-분석-화면-WebSocket-증분-메시지.md`

## 연결

`ws://<api-host>/v1/realtime/analysis`에 연결한다. 모든 메시지는 `version: "1"`, `type`, `sentAt`을 가진다. `sentAt`은 ISO 8601 KST 오프셋을 포함한다.

## 클라이언트 명령

| type | 필수 필드 | 의미 |
|---|---|---|
| `analysis.subscribe` | `instrumentId`, `unit`, `rangeDays` | 관심목록 안의 코인 분석 구독 또는 기존 구독 변경 |

`unit`은 `1m`, `5m`, `10m`, `30m`, `1h`, `1d`, `1w`, `1M` 중 하나다. `rangeDays`는 1, 7, 30, 90, 365, 1095 중 하나다. 서버는 1분·5분·10분·30분·시봉의 장기 구독에서 가장 최근 1,000개 캔들만 반환하고 화면은 반환 범위를 표시한다.

## 서버 메시지

| type | 전송 시점 | 본문 | 크기 규칙 |
|---|---|---|---|
| `analysis.session` | 유효 구독 직후 | `subscriptionId` | 한 번 |
| `analysis.instrument` | 유효 구독 직후 | 거래 상품 식별 정보 | 한 번 |
| `analysis.chart` | 유효 구독 직후 | `unit`, 차트 캔들 청크 | 캔들 500개 이하 |
| `analysis.indicators` | 최초 구독 | `chunkIndex`, `chunkCount`, SMA 20/60, EMA 20, 볼린저 밴드, RSI 14 | 지표 500개 이하 청크 |
| `analysis.indicator.upsert` | 새 캔들 보정 뒤 | 마지막 캔들의 SMA 20/60, EMA 20, 볼린저 밴드, RSI 14 | 기존 지표 배열을 교체하지 않는 단일 지점 갱신 |
| `analysis.market` | 초기 차트 뒤와 시장 데이터 변경 시 | 현재가, 호가 요약, 최근 체결 요약 | 차트 미포함 |
| `analysis.candle.upsert` | 현재 봉이 새로 생기거나 보정될 때 | 단일 캔들 | 단일 캔들만 |
| `analysis.error` | 잘못된 구독 또는 권한 없음 | `code`, `message` | 실패 원인만 |

`analysis.session`은 같은 연결에서 보낸 각 `analysis.subscribe`의 승인 경계다. 서버는 구독 수신 순서대로 `analysis.session`을 먼저 보내고 그 세션의 상품·차트·지표·시장 메시지를 이어서 보낸다. 구독이 실패하면 `analysis.session` 대신 `analysis.error`가 같은 순서의 응답 경계가 된다.

## 재연결과 오류

- 클라이언트는 연결이 닫히면 동일한 `analysis.subscribe`를 다시 보낸다.
- 클라이언트가 같은 연결에서 새 구독을 보내면 직전 구독 세대를 즉시 무효화한다. 새 구독에 대응하는 `analysis.session`을 받을 때까지 이전 세션의 지연 프레임을 화면에 반영하지 않으며, 여러 구독 승인이 대기 중이면 전송 순서대로 세대를 대응한다.
- 관심목록 밖 거래 상품은 `NOT_WATCHLISTED`를 받고 차트·시장 메시지를 받지 않는다.
- 하나의 메시지 오류는 연결을 닫지 않는다. 클라이언트는 오류 문구와 이전에 성공한 화면 상태를 함께 유지한다.
