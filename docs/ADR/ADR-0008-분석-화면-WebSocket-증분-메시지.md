# ADR-0008: 코인 분석 화면의 WebSocket 증분 메시지

Date: 2026-07-14
Status: Accepted
Related Issue: [#12](https://github.com/goodjoon-company/goodmoneying/issues/12)

## 맥락

코인 분석 화면은 관심목록에서 선택한 한 코인의 차트, 거래량, 보조지표, 현재가, 호가, 체결 요약을 동시에 표시한다. 기존 운영 화면의 SSE(Server-Sent Events)는 그대로 유지해야 하며, 분석 화면에 매 초 전체 화면 상태를 재전송하면 장기 차트와 지표가 불필요하게 커진다.

## 결정

- 브라우저 분석 전송은 `/v1/realtime/analysis` WebSocket으로 분리한다.
- 최초 구독은 세션, 상품, 500개 이하 차트 청크, 500개 이하 지표 청크, 시장 요약의 독립 메시지로 보낸다.
- 이후에는 시장 요약을 별도 갱신하고, 새 또는 보정된 봉이 있을 때만 단일 봉과 마지막 지표 한 개를 보낸다.
- 구독 대상은 현재 관심목록으로 제한한다. 유효하지 않은 명령은 연결을 끊지 않고 `analysis.error`로 응답한다.
- 일봉 원천 데이터가 없고 1분 원천 데이터가 있으면 서버가 일봉을 파생한다. 주봉과 월봉은 원천 일봉으로 파생한다.
- 고빈도 시간 단위는 서버가 가장 최근 1,000개로 제한하며 화면에 표시 개수와 제한을 명시한다.
- 같은 연결의 구독 변경은 `analysis.session`을 구독 세대 승인 경계로 삼는다. 클라이언트는 최신 세대 승인 전 이전 구독의 지연 증분 메시지를 폐기한다.

## 결과

- 기존 SSE 운영 화면과 업비트 수집 WebSocket은 변경하지 않는다.
- 분석 화면의 WebSocket 프록시는 Vite와 Nginx에서 업그레이드 헤더를 보존해야 한다.
- 메시지 형식은 `docs/contracts/api/realtime-analysis-websocket.schema.json`으로 검증한다.
