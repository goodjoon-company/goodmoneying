# API 계약

HTTP API 또는 외부 노출 interface의 source of truth를 둔다.

## 권장 파일

- `openapi.yaml`: REST/HTTP API 기준
- GraphQL을 쓰는 경우 `schema.graphql`

## 기록 기준

- path, method, request, response, error model, auth requirement를 계약 파일에 기록한다.
- breaking change는 ADR 후보로 본다.
