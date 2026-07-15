import pytest

from goodmoneying_upbit_gateway.safety import PolicyBlocked, SafetyPolicy


def test_only_read_and_official_test_levels_may_reach_upstream() -> None:
    policy = SafetyPolicy()

    policy.ensure_upstream_allowed("read")
    policy.ensure_upstream_allowed("test")
    with pytest.raises(PolicyBlocked, match="비파괴"):
        policy.ensure_upstream_allowed("blocked")
