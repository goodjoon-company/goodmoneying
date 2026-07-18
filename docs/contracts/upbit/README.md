# 업비트 외부 API 계약

[`upbit-api-catalog.yaml`](upbit-api-catalog.yaml)은 업비트 개발자 센터 v1.6.3의 `llms.txt`와 개별 공식 마크다운(markdown)을 2026-07-16에 확인한 REST·WebSocket 기능 카탈로그다. REST는 활성 50개와 사용 중단(deprecated) 1개를 구분하고, WebSocket은 데이터 스트림 14개와 `LIST_SUBSCRIPTIONS` 운용 1개를 분리한다. 기능 식별자, 메서드·경로, 파라미터 타입·필수 여부, 요청 제한 그룹, 비파괴 안전 등급의 단일 기준(source of truth)이다.

공식 문서가 변경되면 이 계약과 자동 계약 테스트를 먼저 갱신한다. `blocked` 항목은 화면 표시와 로컬 요청 미리보기만 허용하며 어떤 환경에서도 업비트 상향 전송 대상으로 해석하지 않는다.

페어 목록 조회의 현재 상세 응답은 `market_event.warning`과 `market_event.caution`을 사용한다. 목록에 존재하는 페어는 활성 상태로 취급하고, 경고·유의 여부는 내부 경고 수준으로 별도 정규화하며 `market_event` 원문은 manifest에 그대로 보존한다.

공식 교환 API 요청 제한 그룹명은 `default`를 그대로 사용한다. `Origin` 헤더 제한은 발동 조건(`trigger`), 적용 대상(`applies_to`), 그룹(`group`), 한도(`limit`)를 분리해 일반 그룹 제한과 혼동하지 않는다.

wheel 배포본은 같은 내용을 `apps/upbit_gateway/goodmoneying_upbit_gateway/data/upbit-api-catalog.yaml`에 패키지 데이터(package data)로 포함한다. 이 복사본은 배포 산출물이며 직접 편집하지 않는다. 공식 계약을 갱신한 뒤 다음 명령으로 복사하고 계약 동등성 테스트를 실행한다.

```bash
cp docs/contracts/upbit/upbit-api-catalog.yaml apps/upbit_gateway/goodmoneying_upbit_gateway/data/upbit-api-catalog.yaml
uv run pytest -q tests/contracts/test_upbit_gateway_contract.py
```

## P6 private 주문 대사 계약

- [`myorder-event.md`](myorder-event.md): private `myOrder` WebSocket event를 내부 대사 입력으로 해석하는 계약이다. initial snapshot 없음, 무이벤트 정상 처리, SMP 필드 보존, 재주문 금지를 고정한다.
- [`rest-order-reconciliation.md`](rest-order-reconciliation.md): 주문조회 권한이 있는 Upbit REST 주문 snapshot을 내부 대사 원장 입력으로 정규화하는 계약이다. 실제 REST 호출 없이 이미 수신한 snapshot만 처리하며 terminal snapshot만 기존 원장에 적용한다.
