# Message 계약

내부 메시지, event, stream payload의 source of truth를 둔다.

## 권장 파일

- `*.proto`: Protobuf를 쓰는 경우
- repo가 다른 schema 포맷을 선택하면 이 README와 `docs/02_Architecture.md`의 계약 위치를 함께 갱신한다.

## 기록 기준

- message field, subject/topic naming, compatibility policy를 이 위치에서 관리한다.
- event subject만 있는 경우에도 Architecture 문서에 장황하게 복사하지 않고 이 위치에서 기준을 둔다.
