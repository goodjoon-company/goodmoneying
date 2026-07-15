from goodmoneying_upbit_gateway.trace import MASK, sanitize


def test_trace_recursively_masks_credentials_jwt_hash_and_sensitive_values() -> None:
    source = {
        "Authorization": "Bearer fake.jwt.token",
        "parameters": {
            "access_key": "fake-access",
            "query_hash": "abc123",
            "nested": [{"secret_key": "fake-secret"}, {"memo": "fake-secret"}],
        },
    }

    sanitized = sanitize(
        source,
        sensitive_values={"fake.jwt.token", "fake-access", "abc123", "fake-secret"},
    )

    assert sanitized == {
        "Authorization": MASK,
        "parameters": {
            "access_key": MASK,
            "query_hash": MASK,
            "nested": [{"secret_key": MASK}, {"memo": MASK}],
        },
    }
