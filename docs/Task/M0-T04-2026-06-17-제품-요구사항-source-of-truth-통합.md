# M0-T04-2026-06-17-제품-요구사항-source-of-truth-통합

Status: Done
Created: 2026-06-17
Updated: 2026-06-17
Owner: Codex

## 목표

`docs/01_Product.md`와 별도 브레인스토밍 설계 산출물에 제품 요구사항이 중복 source of truth처럼 공존하는 문제를 정리한다.

## 요구사항 링크

- User request: 제품 요구사항이 superpowers로 만든 것과 goodjoon-workflow에서 만든 것이 두 개 공존하는 문제 해결
- Product source of truth: `docs/01_Product.md`
- Design evidence: `docs/History/2026-06-17-superpowers-설계근거-흡수.md`

## 범위

- 포함: `docs/01_Product.md`를 단일 제품 기준으로 갱신
- 포함: superpowers 설계 근거를 제품 기준과 분리해 상태를 정리
- 포함: Task/Test/History 증적 추가
- 제외: 아키텍처(Architecture), 계약(Contract), 코드 구현, 프론트엔드(Frontend) 기술 스택 결정

## 현재 맥락

`docs/01_Product.md`는 제품 source of truth로 지정되어 있지만, 별도 브레인스토밍 설계 산출물도 제품 요구사항처럼 읽힐 수 있었다. 특히 2026-06-16 설계 근거는 넓은 MVP를 담고 있고, 2026-06-17 설계 근거는 좁힌 MVP와 M0~M10 마일스톤을 담고 있어 장기적으로 drift 위험이 있었다.

## 설계 메모

- 현재 제품 요구사항, 정책, 로드맵은 `docs/01_Product.md`만 기준으로 삼는다.
- 브레인스토밍 설계 근거는 의사결정 과정과 승인 근거를 보존하는 참고 기록으로 제한한다.
- 2026-06-16 설계 명세는 2026-06-17 명세로 대체됐음을 명시한다.
- 2026-06-17 설계 명세는 `docs/01_Product.md`에 반영됐음을 명시한다.

## 계약 링크

- 계약 변경 없음
- 후속 구현 전 시장 데이터, 호가(Orderbook), 데이터 품질(Data Quality), API 계약(Contract)을 `docs/contracts/`에 추가해야 한다.

## 완료 기준

- `docs/01_Product.md`가 제품 요구사항의 단일 source of truth임을 명시한다.
- `docs/01_Product.md`에 최신 MVP, 최종 제품 범위, 요구사항, 마일스톤, 프론트엔드 화면 로드맵이 반영된다.
- 2026-06-16 superpowers 설계 근거가 대체됐음이 남아 있다.
- 2026-06-17 superpowers 설계 근거가 `docs/01_Product.md`에 반영됐음이 남아 있다.
- 검증 증적과 인계 기록이 남아 있다.

## 검증

- `! rg -n "TBD|TODO" docs/01_Product.md docs/History/2026-06-17-superpowers-설계근거-흡수.md`
- `rg -n "source of truth|대체|반영|이 문서가 다르면|현재 제품 요구사항" docs/01_Product.md docs/History/2026-06-17-superpowers-설계근거-흡수.md`
- `rg -n "GM-PROD-00[1-9]|GM-PROD-01[0-8]|M10|국내 주식|미국 주식|LLM|봇|모의매매|실거래|프론트엔드" docs/01_Product.md`
- `git diff --check`

## 추적성

- Related Test: `docs/Test/2026-06-17-제품-요구사항-source-of-truth-통합-검증.md`
- Related History: `docs/History/2026-06-17-제품-요구사항-source-of-truth-통합.md`
