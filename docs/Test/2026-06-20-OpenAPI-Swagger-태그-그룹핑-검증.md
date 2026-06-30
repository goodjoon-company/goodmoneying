# 2026-06-20 OpenAPI Swagger 문서화 검증

## 범위

- API 계약(Contract): `docs/contracts/api/openapi.yaml`
- 검증 대상: Swagger UI/Viewer에서 기본 그룹(default)으로 묶이지 않도록 OpenAPI 태그(Tags)와 operation 태그를 제공한다.
- 검증 대상: Swagger UI/Viewer의 스키마(Schema) 목록에서 각 component schema의 역할을 설명한다.

## 검증 결과

| 명령 | 결과 | 비고 |
| --- | --- | --- |
| `uv run pytest tests/contracts/test_api_contract.py::test_openapi_contract_groups_operations_with_described_tags -q` | 실패 확인 | 변경 전 `tags`가 없어 `KeyError: 'tags'` 발생 |
| `uv run pytest tests/contracts/test_api_contract.py::test_openapi_component_schemas_have_descriptions -q` | 실패 확인 | 변경 전 41개 component schema에 `description` 부재 |
| `ruby -e 'require "yaml"; c=YAML.load_file("docs/contracts/api/openapi.yaml"); missing=[]; c.fetch("components").fetch("schemas").each { \|name, schema\| missing << name unless schema["description"].to_s.strip != "" }; abort("missing: #{missing.join(", ")}") unless missing.empty?; puts "schema descriptions ok: #{c["components"]["schemas"].size}"'` | 통과 | 41개 component schema 설명 존재 확인 |
| `uv run pytest tests/contracts/test_api_contract.py -q` | 통과 | 6개 계약 테스트 통과 |
| `npm run e2e -- --project=chromium` | 통과 | Playwright Chromium E2E 1개 통과 |

## 리뷰

- 요구사항 적합성: Swagger Viewer 가독성 개선 요구에 맞게 전역 태그 설명, 각 API operation 태그, component schema 설명을 추가했다.
- 아키텍처 적합성: REST/HTTP API의 기계 검증 가능한 계약은 `docs/contracts/api/openapi.yaml`에 둔다는 아키텍처 원칙을 유지했다.
- 계약 적합성: 응답 스키마(Schema), 요청 본문, 경로(Path), 인증(Security)은 변경하지 않고 문서 메타데이터만 확장했다.
- 잔여 리스크: 외부 Swagger Viewer 종류에 따라 태그 표시 순서나 접힘 UI는 도구별로 다를 수 있으나, OpenAPI 표준 `tags`/operation `tags` 구조는 제공된다.
