from threading import Event, Thread

from goodmoneying_upbit_gateway.rate_limit import GroupRateLimiter, parse_remaining_req


def test_official_group_limit_waits_until_window_opens() -> None:
    now = [0.0]
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        now[0] += seconds

    limiter = GroupRateLimiter(clock=lambda: now[0], sleep=sleep)
    for _ in range(8):
        limiter.acquire("order-test")
    limiter.acquire("order-test")

    assert waits == [1.0]


def test_remaining_req_parser_uses_group_and_second_quota() -> None:
    assert parse_remaining_req("group=order-test; min=1800; sec=0") == ("order-test", 0)
    assert parse_remaining_req("invalid") is None
    assert parse_remaining_req("sec=3; group=default") == ("default", 3)


def test_explicit_429_or_418_cooldown_is_applied_before_next_request() -> None:
    now = [5.0]
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        now[0] += seconds

    limiter = GroupRateLimiter(clock=lambda: now[0], sleep=sleep)
    limiter.defer("default", 3.0)
    limiter.acquire("default")

    assert waits == [3.0]


def test_waiting_group_does_not_block_an_independent_group() -> None:
    now = [0.0]
    sleeping = Event()
    release_sleep = Event()
    market_acquired = Event()

    def sleep(seconds: float) -> None:
        sleeping.set()
        release_sleep.wait(timeout=1)
        now[0] += seconds

    limiter = GroupRateLimiter(clock=lambda: now[0], sleep=sleep)
    for _ in range(8):
        limiter.acquire("order-test")
    waiting = Thread(target=lambda: limiter.acquire("order-test"))
    waiting.start()
    assert sleeping.wait(timeout=1)

    def acquire_market() -> None:
        limiter.acquire("market")
        market_acquired.set()

    market = Thread(target=acquire_market)
    market.start()
    acquired_without_order_release = market_acquired.wait(timeout=0.1)
    release_sleep.set()
    market.join(timeout=1)
    waiting.join(timeout=1)

    assert acquired_without_order_release
