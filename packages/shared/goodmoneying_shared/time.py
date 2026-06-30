from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def _as_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def now_kst() -> datetime:
    return datetime.now(KST).replace(microsecond=0)


def minute_bucket(value: datetime | str) -> datetime:
    value = _as_datetime(value).astimezone(KST)
    return value.replace(second=0, microsecond=0)


def isoformat_kst(value: datetime | str) -> str:
    return _as_datetime(value).astimezone(KST).replace(microsecond=0).isoformat()


def minutes_ago(minutes: int) -> datetime:
    return now_kst() - timedelta(minutes=minutes)
