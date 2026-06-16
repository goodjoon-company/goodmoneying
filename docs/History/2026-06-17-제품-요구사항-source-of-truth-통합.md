# 2026-06-17-제품-요구사항-source-of-truth-통합

Date: 2026-06-17
Related Task: `docs/Task/M0-T04-2026-06-17-제품-요구사항-source-of-truth-통합.md`
Related PR: 없음

## 변경 요약

제품 요구사항 source of truth를 `docs/01_Product.md` 하나로 통합했다. 별도 브레인스토밍 설계 산출물은 현재 제품 기준이 아니라 의사결정 과정과 승인 근거를 보존하는 참고 기록으로 역할을 제한했다.

## 영향 문서

- `docs/01_Product.md`: 최신 제품 범위, MVP, 요구사항, 프론트엔드(Frontend) 화면 로드맵, M0~M10 마일스톤을 단일 제품 기준으로 반영
- `docs/History/2026-06-17-superpowers-설계근거-흡수.md`: 2026-06-16 설계 근거와 2026-06-17 재수립 근거를 후속 정리에서 흡수
- `docs/Task/M0-T04-2026-06-17-제품-요구사항-source-of-truth-통합.md`: 실행 단위 기록 추가
- `docs/Test/2026-06-17-제품-요구사항-source-of-truth-통합-검증.md`: 검증 증적 추가

## 영향 계약

- DB/API/message 계약(Contract) 변경은 없다.
- 후속 구현 전 시장 데이터, 호가(Orderbook), 데이터 품질(Data Quality), 화면 API 계약을 `docs/contracts/`에 추가해야 한다.

## 검증

- 제품 기준과 superpowers 설계 명세에서 미완성 자리표시자가 없는지 확인했다.
- `docs/01_Product.md`가 단일 source of truth임을 명시하는지 확인했다.
- superpowers 설계 근거가 제품 기준과 분리됐는지 확인했다.
- 원래 요구사항이 MVP 또는 후속 마일스톤에서 누락되지 않았는지 확인했다.
- `git diff --check`로 공백 오류가 없는지 확인했다.

## 리스크

- `docs/01_Product.md`는 제품 기준만 담고 있으며, 실제 데이터 계약과 아키텍처(Architecture)는 아직 구체화되지 않았다.
- 프론트엔드 기술 스택은 제품 문서가 아니라 후속 아키텍처 문서 또는 ADR(Architecture Decision Record)에서 결정해야 한다.

## 후속 작업

- `docs/02_Architecture.md`와 모듈 설계 문서에 M1 업비트 수집 운영 MVP 구조를 정의한다.
- `docs/contracts/`에 업비트 시장 데이터, 호가 요약, 데이터 품질, 운영 대시보드 API 계약을 추가한다.
- M1 구현 Task를 작성할 때 `docs/01_Product.md`의 GM-PROD-001부터 GM-PROD-005를 요구사항 링크로 사용한다.
