# 시스템 트레이딩 UI 품질 검증(UI QA)

## 자동·수동 gate

- viewport: 1440, 1280, 1024, 900, 760, 390, 360
- navigation: desktop expanded·compact, mobile open·closed
- state: data, empty, loading, error, permission denied, stale, disconnected, partial
- overflow: 긴 한글 market명, 20자리 decimal, timezone, error detail
- keyboard: skip link, navigation, table, dialog, graph alternative, kill switch
- screen reader: landmark, heading, live region rate, form label·error, chart table alternative
- contrast: normal 4.5:1, large 3:1, non-text control·focus 3:1
- touch target: WCAG 2.2 2.5.8과 제품 최소 44px
- reduced motion: animation 제거 후 정보 손실 없음
- browser: console error 0, unexpected network error 0, WebSocket gap recovery
- screenshot: route·viewport·critical state별 증적 저장

## 성능 gate

- p75 LCP ≤ 2.5s, INP ≤ 200ms, CLS ≤ 0.1
- first useful shell·core status ≤ 3s
- 정상 event의 browser 반영 목표 ≤ 1s
- route·chart chunk, total compressed bundle과 React render profile 기록
- initial route의 full history download와 event별 full refetch 금지

## 완료 보고 형식

각 route는 checked viewport, overflow·collision, keyboard·screen reader, contrast, console·network, performance 결과와 수정 내역을 `docs/Test/`에 기록한다. 측정하지 못한 항목은 통과로 표시하지 않고 이유와 후속 Issue를 남긴다.
