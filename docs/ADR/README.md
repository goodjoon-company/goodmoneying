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

- [ADR-0011: 업비트 API 게이트웨이와 비파괴 테스트 경계](ADR-0011-업비트-API-게이트웨이와-비파괴-테스트-경계.md)
