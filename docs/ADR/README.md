# ADR

Architecture Decision Record는 "왜 이 설계가 되었는가"를 기록한다. 현재 시스템이 어떻게 동작하는지는 `docs/02_Architecture.md`와 모듈 설계 문서에 기록한다.

## 작성 대상

- 되돌리기 어려운 기술 선택
- 여러 시스템, 데이터, 보안, 운영, 비용에 영향이 있는 선택
- DB/API/message 계약의 breaking change
- 대안과 trade-off를 나중에 다시 설명해야 하는 선택

## 파일 규칙

- 파일명: `ADR-0001-한글-결정-제목.md`
- 상태: `Proposed`, `Accepted`, `Superseded`, `Rejected`
- 새 ADR은 `docs/ADR/템플릿.md`를 복사해 작성한다.

## 운영 규칙

- ADR에는 진행 로그나 테스트 결과를 넣지 않는다.
- 기존 ADR을 수정해 과거 결정을 덮어쓰기보다, 필요하면 새 ADR로 대체 관계를 기록한다.

## 현재 결정

- [ADR-0012: 시스템 트레이딩 플랫폼과 PostgreSQL 실행 기반](ADR-0012-시스템-트레이딩-플랫폼과-PostgreSQL-실행-기반.md)
- [ADR-0013: UTC 원천 시각, 결측 의미와 재현성](ADR-0013-UTC-원천-결측-재현성.md)
- [ADR-0014: 실거래 기본 비활성과 주문 안전 경계](ADR-0014-실거래-기본-비활성과-주문-안전-경계.md)
- [ADR-0015: 승인 SHA, 백업·복구와 배포 안전](ADR-0015-승인-SHA-백업-복구와-배포-안전.md)
- [ADR-0016: KRW 자동 수집 정책과 PostgreSQL 내구성 조정](ADR-0016-KRW-자동수집정책과-PostgreSQL-내구성-조정.md)
- [ADR-0017: 캔들 개정 원장과 버전 집계 계보](ADR-0017-캔들-개정-원장과-버전-집계-계보.md)

ADR-0003, ADR-0004, ADR-0007, ADR-0011의 유효한 기반은 유지하되 위 결정과 충돌하는 KST 내부 계산, 활성 50·수동 시작, 단일 worker 고정, 보호 없는 release push, 전략·거래 보류, 실제 주문 코드 전면 차단 정책은 대체됐다. ADR-0002의 PostgreSQL 단일 저장소 기반은 ADR-0012가 계승하고 시간대 결정은 ADR-0013이 대체한다.
