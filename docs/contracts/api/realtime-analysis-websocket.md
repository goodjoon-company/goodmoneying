# 코인 분석 WebSocket 계약

Related Schema: `realtime-analysis-websocket.schema.json`, `internal-realtime-stream.schema.json`
Related ADR: `docs/ADR/ADR-0008-분석-화면-WebSocket-증분-메시지.md`, `docs/ADR/ADR-0019-버전-지표와-시장-통계-불변-물질화.md`

## 연결

`ws://<api-host>/v1/realtime/analysis/stream`에 연결한다. 전환기 호환을 위해 서버는 같은 handler를 `ws://<api-host>/v1/realtime/analysis`에도 유지한다. P2-7 이후 서버 메시지는 내부 스트림 envelope를 우선 계약으로 사용한다. 모든 메시지는 `schema_version: "1.0"`, `topic`, `scope`, `event_id`, `sequence`, `cursor`, `occurred_at`, `published_at`, `message_type`, `payload`를 가진다. 전환기 호환을 위해 `payload` 안의 legacy `version: "1"`, `type`, `sentAt`을 top-level에도 복제한다. 신규 클라이언트는 `/stream` alias와 `payload`를 사용하고 top-level legacy 필드를 상태 반영 기준으로 사용하지 않는다.

## 클라이언트 명령

| type | 필수 필드 | 의미 |
|---|---|---|
| `analysis.subscribe` | `instrumentId`, `unit`, `rangeDays` | 관심목록 안의 코인 분석 구독 또는 기존 구독 변경 |

`unit`은 `1m`, `3m`, `5m`, `10m`, `15m`, `30m`, `1h`, `4h`, `1d`, `1w`, `1M` 중 하나다. `rangeDays`는 1, 7, 30, 90, 365, 1095 중 하나다. 서버는 분·시간 봉의 장기 구독에서 반개방 종료 시각보다 앞선 가장 최근 1,000개 캔들만 반환하고, 저장 지표를 같은 시각·순서로 정렬하며 화면은 반환 범위를 표시한다. 각 캔들은 계산 버전, 원천·지식 시각, 입력 내용 해시, 5단계 품질과 완전성을 포함한다.

## 서버 메시지

| type | 전송 시점 | 본문 | 크기 규칙 |
|---|---|---|---|
| `analysis.session` | 유효 구독 직후 | `subscriptionId` | `message_type=subscribed`, sequence 1 |
| `analysis.instrument` | 유효 구독 직후 | 거래 상품 식별 정보 | 한 번 |
| `analysis.chart` | 유효 구독 직후 | `unit`, 차트 캔들 청크 | 캔들 500개 이하 |
| `analysis.indicators` | 최초 구독 또는 과거 지표 개정 | `chunkIndex`, `chunkCount`, 선택적 `revisionRefresh`, 버전 지표 지점 | 지표 500개 이하 청크 |
| `analysis.indicator.upsert` | 최신 지표 append 또는 마지막 지점 교체 | 최신 캔들과 `startedAt`이 같은 단일 버전 지표 지점 | 기존 지표 배열을 교체하지 않는 단일 지점 갱신 |
| `analysis.microstructure` | 최초 구독 또는 과거 1분 미시구조 정정 | `chunkIndex`, `chunkCount`, 선택적 `revisionRefresh`, 저장 물질화 지점 | 500개 이하 청크; 1분 외 구독은 빈 청크 |
| `analysis.microstructure.upsert` | 최신 저장 1분 물질화 append 또는 마지막 지점 교체 | 최신 1분 캔들과 `startedAt`이 같은 단일 미시구조 지점 | 요청 시 계산하지 않는 단일 지점 갱신 |
| `analysis.market` | 초기 차트 뒤와 시장 데이터 변경 시 | 현재가, 호가 요약, 최근 체결 요약 | 차트 미포함 |
| `analysis.candle.upsert` | 현재 봉이 새로 생기거나 보정될 때 | 단일 캔들 | 단일 캔들만 |
| `analysis.error` | 잘못된 구독 또는 권한 없음 | `code`, `message` | 실패 원인만 |
| `stream.heartbeat` | 연결 유지와 복구 기준 확인 | `lastSequence`, `serverTime` | `message_type=heartbeat`, event sequence 증가 없음 |
| `analysis.snapshot_required` | resume cursor 위변조·만료·문맥 불일치 또는 클라이언트 gap | `code`, `message`, `snapshotTopic` | P2-8 REST snapshot 복구 전까지 이후 event 적용 금지 |

`analysis.session`은 같은 연결에서 보낸 각 `analysis.subscribe`의 승인 경계다. 서버는 구독 수신 순서대로 `analysis.session`을 먼저 보내고 그 세션의 상품·차트·지표·시장 메시지를 이어서 보낸다. 구독이 실패하면 `analysis.session` 대신 `analysis.error`가 같은 순서의 응답 경계가 된다.

지표 지점은 SMA20·SMA60·EMA20·볼린저 상단/중앙/하단·RSI14 값, 지표별 `warming_up|ready|missing` 계산 상태, 정의 버전 해시, `materializationId`, 원천·품질 프런티어, `knowledgeAt`, `sourceAsOf`를 포함한다. 준비 기간에는 값이 `null`이고 상태가 `warming_up`이며 0 또는 미래 값으로 채우지 않는다.

서버는 최신 캔들이 append되거나 마지막 캔들이 보정되고 저장된 최신 지표의 `startedAt`이 그 캔들과 같을 때만 `analysis.indicator.upsert`를 보낸다. 지표 워커가 늦어 최신 지표가 캔들보다 오래됐으면 오래된 지표 upsert를 보내지 않는다. 이전 지표 배열의 과거 지점이 달라졌을 때만 `revisionRefresh: true`인 전체 `analysis.indicators` 청크를 보내며, 단순 최신 append/교체 뒤에는 중복 전체 갱신을 보내지 않는다.

미시구조 지점은 `microstructure-v1` 계산 버전, 호가 spread·bps·상위 10단계 잔량·불균형, 공식 매수(`BID`)·매도(`ASK`) 표시 체결 건수·수량·불균형·체결 강도, 계산 상태, 호가·체결 5단계 원천 품질, 원천 캔들 개정, receipt·호가·체결·연결 품질 프런티어와 `sourceAsOf`·`knowledgeAt`을 포함한다. 공격자(aggressor)나 메이커·테이커 의미를 추론하지 않는다. 0 분모와 불완전 입력은 `null`과 명시적 상태로 보내며 0·무한대·상한값으로 바꾸지 않는다. 필드 의미는 [Upbit WebSocket 체결](https://docs.upbit.com/kr/reference/websocket-trade)과 [Upbit WebSocket 호가](https://docs.upbit.com/kr/reference/websocket-orderbook)의 최신 공식 문서를 따른다.

`analysis.microstructure`와 upsert는 PostgreSQL에 저장된 불변 물질화만 읽는다. 최신 지점이 최신 1분 캔들과 정렬될 때만 upsert하고, 워커가 늦으면 오래된 지점을 보내지 않는다. 과거 지점이 바뀌면 `revisionRefresh: true` 전체 청크를 보낸다.

## P2 envelope와 cursor

- topic 형식은 분석 구독별 `analysis.instrument:{instrumentId}:{unit}:{rangeDays}`다.
- scope는 운영자 경계인 `operator:local`로 시작한다.
- `subscribed`는 sequence 1, `event`는 topic·scope 안에서 1씩 증가, `heartbeat`는 마지막 sequence를 반복한다.
- `cursor`는 topic, scope, sequence, snapshot version, issued/expires 시각을 HMAC-SHA256으로 서명한 불투명 값이다.
- `resumeCursor` 또는 `resume_cursor`가 위변조·만료·다른 topic/scope이면 서버는 `message_type=snapshot_required`와 `analysis.snapshot_required` payload를 보낸다.
- stream subscribe 명령이 보낸 `topic` 또는 `scope`가 payload에서 계산한 topic/scope와 다르면 `analysis.error` `INVALID_TOPIC`으로 거부한다.
- 운영 모드는 `GOODMONEYING_STREAM_CURSOR_SECRET`이 반드시 설정돼야 한다. 개발·테스트 모드만 로컬 기본 secret을 사용한다.
- 브라우저는 중복·역순 event를 버리고, sequence gap을 감지하면 REST snapshot 복구 전까지 이후 event를 reducer에 적용하지 않는다.
- 브라우저는 heartbeat가 마지막 적용 sequence보다 앞선 sequence를 보고해도 같은 gap으로 보고 REST snapshot 복구 전까지 이후 event를 적용하지 않는다.
- P2-8 전까지 REST snapshot endpoint와 slow consumer backpressure는 아직 제공하지 않는다.

## 재연결과 오류

- 클라이언트는 연결이 닫히면 동일한 `analysis.subscribe`를 다시 보낸다.
- 클라이언트는 P2 envelope가 있으면 `payload`를 상태 reducer 입력으로 사용한다.
- 클라이언트가 같은 연결에서 새 구독을 보내면 직전 구독 세대를 즉시 무효화한다. 새 구독에 대응하는 `analysis.session`을 받을 때까지 이전 세션의 지연 프레임을 화면에 반영하지 않으며, 여러 구독 승인이 대기 중이면 전송 순서대로 세대를 대응한다.
- 관심목록 밖 거래 상품은 `NOT_WATCHLISTED`를 받고 차트·시장 메시지를 받지 않는다.
- 하나의 메시지 오류는 연결을 닫지 않는다. 클라이언트는 오류 문구와 이전에 성공한 화면 상태를 함께 유지한다.
