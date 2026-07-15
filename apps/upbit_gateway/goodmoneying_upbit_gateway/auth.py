from __future__ import annotations

import hashlib
import stat
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlencode
from uuid import uuid4

import jwt

type ParameterValue = str | int | float | bool | Sequence[str | int | float | bool]


@dataclass(frozen=True)
class Credentials:
    access_key: str
    secret_key: str


class CredentialConfigurationError(ValueError):
    pass


def _read_secret_file(value: str) -> str:
    path = Path(value)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise CredentialConfigurationError("자격 증명 파일은 절대 경로의 일반 파일이어야 합니다.")
    if stat.S_IMODE(path.stat().st_mode) & 0o222:
        raise CredentialConfigurationError("자격 증명 파일은 읽기 전용이어야 합니다.")
    secret = path.read_text(encoding="utf-8").strip()
    if not secret:
        raise CredentialConfigurationError("자격 증명 파일이 비어 있습니다.")
    return secret


def load_credentials(environ: Mapping[str, str]) -> Credentials:
    direct = (environ.get("UPBIT_ACCESS_KEY"), environ.get("UPBIT_SECRET_KEY"))
    files = (environ.get("UPBIT_ACCESS_KEY_FILE"), environ.get("UPBIT_SECRET_KEY_FILE"))
    if any(direct) and any(files):
        raise CredentialConfigurationError("환경 변수와 파일 자격 증명 소스를 함께 쓸 수 없습니다.")
    if all(direct):
        return Credentials(access_key=direct[0] or "", secret_key=direct[1] or "")
    if all(files):
        return Credentials(
            access_key=_read_secret_file(files[0] or ""),
            secret_key=_read_secret_file(files[1] or ""),
        )
    raise CredentialConfigurationError("접근 키와 비밀 키를 한 쌍으로 설정해야 합니다.")


def build_query_string(parameters: Sequence[tuple[str, ParameterValue]]) -> str:
    """입력 순서와 배열 키 반복을 보존한 비인코딩 쿼리 문자열을 만든다."""
    return unquote(urlencode(parameters, doseq=True))


def query_hash(query_string: str) -> str:
    return hashlib.sha512(query_string.encode("utf-8")).hexdigest()


def create_jwt(
    credentials: Credentials,
    query_string: str,
    *,
    nonce_factory: Callable[[], object] = uuid4,
) -> str:
    payload = {
        "access_key": credentials.access_key,
        "nonce": str(nonce_factory()),
    }
    if query_string:
        payload.update(
            {
                "query_hash": query_hash(query_string),
                "query_hash_alg": "SHA512",
            }
        )
    return jwt.encode(payload, credentials.secret_key, algorithm="HS512")
