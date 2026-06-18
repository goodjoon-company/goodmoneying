from __future__ import annotations

from datetime import UTC, datetime, timedelta


def now_utc() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def minute_bucket(value: datetime) -> datetime:
    value = value.astimezone(UTC)
    return value.replace(second=0, microsecond=0)


def isoformat_utc(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def minutes_ago(minutes: int) -> datetime:
    return now_utc() - timedelta(minutes=minutes)
