from __future__ import annotations

from fastapi import Header, HTTPException, status


def verify_operator_token(
    expected_token: str,
    x_operator_token: str | None = Header(default=None, alias="X-Operator-Token"),
) -> None:
    if not expected_token:
        return
    if x_operator_token != expected_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "운영 토큰이 없거나 올바르지 않습니다."},
        )
