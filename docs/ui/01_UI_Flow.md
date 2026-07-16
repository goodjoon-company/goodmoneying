# 시스템 트레이딩 UI 흐름(UI Flow)

상태: Accepted Target
구현 상태: 미구현
추적: GitHub Issue #28~#34

## 1. 정보 구조

```text
Command Center
├── Markets
├── Data Lab
├── Coverage & Quality
├── Indicators
├── Strategy Studio
├── Backtest Lab
├── Portfolio
├── Bot Workshop
├── Orders & Fills
├── Risk Center
├── Operations
├── Audit
├── Upbit Lab
└── Settings
```

데스크톱은 왼쪽 rail과 상단 global status strip을 사용한다. 모바일은 상단 app bar, 닫힌 sheet navigation, 고정 emergency action 영역을 사용한다. footer는 build SHA, data freshness, WebSocket 상태, timezone을 표시하고 desktop·mobile 모두 존재한다.

## 2. 핵심 사용자 흐름

### 데이터 준비

`Markets에서 대상 선택 → 정책 편집 → 자동 백필·실시간 가입 → Coverage & Quality에서 상태 확인 → Data Lab에서 dataset version 생성`

별도 시작 버튼은 없다. 정책 저장 결과는 생성된 backfill job, 실시간 desired state, 예상 요청·용량을 함께 보여준다.

### 전략 연구

`Data Lab dataset → Indicator 정의 → Strategy Studio graph 작성·검증 → 불변 version 게시 → Backtest Lab 실행·비교`

검증 실패는 graph node와 error summary 양쪽에 연결한다. pointer를 사용할 수 없는 사용자는 ordered node list와 connection form으로 같은 graph를 편집한다.

### 안전한 봇 운영

`성공 backtest → Portfolio allocation → Bot Workshop paper → shadow → live-ready → 별도 운영 승인 live`

각 승격은 승인 checklist와 실패 이유를 표시한다. 모든 화면에서 global kill switch 상태가 보이며 mobile에서도 두 단계 확인으로 arm할 수 있다. live enable은 일반 UI 동작과 분리하고 이 구현 목표 동안 노출하지 않는다.

### 장애 복구

`global status 경고 → 관련 Operations/Quality/Risk detail → evidence 확인 → 재시도·일시정지·kill switch → Audit에서 actor·reason 확인`

실시간 sequence gap은 snapshot 복구 banner와 마지막 정상 cursor를 표시하고 복구 전 stale 데이터를 정상처럼 표시하지 않는다.

## 3. 화면 상태

모든 route는 loading skeleton, valid empty, recoverable error, permission denied, stale snapshot, WebSocket disconnected, partial data 상태를 정의한다. 빈 상태는 가짜 data를 표시하지 않고 다음 안전 행동을 제공한다.
