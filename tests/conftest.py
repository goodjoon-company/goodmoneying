import os

# 테스트 런타임은 SQLite 축소를 명시적으로 허용한다.
os.environ.setdefault("GOODMONEYING_RUNTIME_MODE", "test")
