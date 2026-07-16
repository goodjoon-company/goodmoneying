# 시스템 트레이딩 UI 사양(UI Spec)

상태: Accepted Target
구현 상태: 미구현
추적: GitHub Issue #28~#34

## 1. 디자인 방향

산업 정밀(Industrial Precision) 편집형 UI를 선택한다. 거래 설비의 신뢰·상태 가독성과 기술 저널의 정보 계층을 결합하며 장식보다 비교, 정렬, 원인과 결과를 우선한다.

의도적으로 배제: Inter·Roboto·Arial 기본 의존, 보라색 gradient, neon cyberpunk, card 안 card, 모든 요소의 round rectangle, gray-on-color text, 의미 없는 glass·parallax. motion은 state transition과 인과 흐름에만 사용하고 reduced-motion에서 제거한다.

## 2. 디자인 token

| token | 값 | 용도 |
|---|---|---|
| `--ink-950` | `#151713` | 주 text·dark surface |
| `--graphite-850` | `#242722` | rail·dense instrument surface |
| `--paper-050` | `#F2EFE6` | 기본 canvas |
| `--paper-100` | `#E8E3D7` | 구획 surface |
| `--line-300` | `#C5BFAF` | divider·grid |
| `--signal-cyan` | `#087E82` | 실행·stream |
| `--safe-green` | `#2E6F45` | 승인·정상 |
| `--caution-amber` | `#B36A00` | 주의·pending |
| `--loss-red` | `#A43B32` | 손실·차단·위험 |

색상은 상태 text·icon·pattern과 함께 사용한다. spacing base는 4px, control 최소 높이는 desktop 36px·touch 44px, radius는 0·2·6px만 사용한다. elevation은 overlay와 sticky header에만 사용한다.

한글 body는 라이선스가 허용된 `Pretendard` self-host subset, 숫자·code는 `IBM Plex Mono` self-host를 우선 검토한다. 실제 license·font file을 저장소에 포함하기 전 재검증한다. 모든 수치는 `font-variant-numeric: tabular-nums slashed-zero`를 사용한다.

## 3. layout

- 1440 이상: 248px navigation, 12-column data grid, persistent inspector
- 1280: 216px navigation, inspector overlay 가능
- 1024·900: compact rail, detail drawer, table column priority
- 760: mobile transition, navigation sheet, two-column summary
- 390·360: one-column, horizontal data table 대신 priority rows·detail disclosure; emergency action은 viewport를 가리지 않음

표는 header sticky, row virtualized, 수치 decimal alignment를 사용한다. chart는 고정 aspect ratio와 table alternative를 제공한다. graph editor는 canvas와 accessible ordered-list representation을 같은 model에서 만든다.

## 4. 상호작용

- focus ring은 2px signal-cyan과 2px offset, 위험 surface에서는 paper 색 보조 outline을 사용한다.
- destructive·live 관련 action은 icon만 두지 않고 동사·대상·영향을 표시한다.
- kill switch arm은 대상·진행 주문 정책·사유를 확인하고 release보다 시각 우선순위가 높다.
- live-disabled는 성공 green이 아니라 안전 잠금 상태로 표현한다.
- WebSocket gap·stale data는 마지막 정상 시각과 snapshot 복구 상태를 표시한다.
- drag는 keyboard move·connect·delete command와 동등해야 한다.

## 5. motion·성능

route 전환은 120~180ms opacity·transform만 사용한다. streaming 수치 변경은 400ms 이내 한 번 highlight하고 무한 pulse를 금지한다. `prefers-reduced-motion: reduce`에서는 transition을 제거한다. chart·graph는 route lazy load, 긴 list는 virtualization, 초기 route는 핵심 status만 요청한다.
