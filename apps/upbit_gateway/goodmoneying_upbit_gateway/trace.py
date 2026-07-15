from __future__ import annotations

from collections.abc import Mapping, Set
from typing import Any

MASK = "***"
SENSITIVE_KEYS = {
    "authorization",
    "access_key",
    "secret_key",
    "jwt",
    "token",
    "query_hash",
    "query_hash_alg",
}


def sanitize(value: Any, *, sensitive_values: Set[str] = frozenset()) -> Any:
    if isinstance(value, Mapping):
        return {
            key: MASK
            if str(key).lower() in SENSITIVE_KEYS
            else sanitize(item, sensitive_values=sensitive_values)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize(item, sensitive_values=sensitive_values) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize(item, sensitive_values=sensitive_values) for item in value)
    if isinstance(value, str):
        result = value
        for sensitive in sorted(sensitive_values, key=len, reverse=True):
            if sensitive:
                result = result.replace(sensitive, MASK)
        return result
    return value
