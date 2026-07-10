# goodmoneying

이 저장소는 제품, 아키텍처(Architecture), 계약(Contract), 작업, 검증, 인계 기록의 단일 기준(source of truth)을 구분한다.

## 문서 지도

| 목적 | 위치 |
|---|---|
| 도메인 용어 단일 기준 | `../UBIQUITOUS_LANGUAGE.md` |
| 제품 단일 기준 | `01_Product.md` |
| 아키텍처 단일 기준 | `02_Architecture.md` |
| 모듈 설계 문서 | `02_Architecture/` |
| DB/API/메시지 계약 | `contracts/` |
| 아키텍처 결정 기록(ADR, Architecture Decision Record) | `ADR/` |
| 마일스톤(Milestone) 작업 요약 | `Task/` |
| 테스트 명세와 보고서 | `Test/` |
| 인계와 변경 이력 | `History/` |

새 작업은 GitHub Issue에서 시작한다. 인터페이스가 바뀌면 코드보다 계약을 먼저 갱신하고, 검증 증적은 `docs/Test/`, 인계 맥락은 `docs/History/`에 기록한다. `docs/Task/`에는 짧은 마일스톤 요약만 둔다.
