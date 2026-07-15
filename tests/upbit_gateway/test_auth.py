from __future__ import annotations

import hashlib
import os
from pathlib import Path

import jwt
import pytest

from goodmoneying_upbit_gateway.auth import (
    CredentialConfigurationError,
    Credentials,
    ParameterValue,
    build_query_string,
    create_jwt,
    load_credentials,
    query_hash,
)


def test_query_hash_uses_unencoded_original_parameter_order_and_repeated_array_keys() -> None:
    parameters: list[tuple[str, ParameterValue]] = [
        ("market", "KRW-BTC"),
        ("states[]", ["wait", "watch"]),
        ("memo", "한글 값"),
        ("include_expired", True),
    ]

    query_string = build_query_string(parameters)

    assert query_string == (
        "market=KRW-BTC&states[]=wait&states[]=watch&memo=한글+값&include_expired=true"
    )
    assert query_hash(query_string) == hashlib.sha512(query_string.encode("utf-8")).hexdigest()


def test_jwt_uses_hs512_fresh_nonce_and_optional_query_hash() -> None:
    credentials = Credentials(access_key="fake-access-key", secret_key="f" * 64)
    nonces = iter(["nonce-1", "nonce-2"])
    first = create_jwt(credentials, "market=KRW-BTC", nonce_factory=lambda: next(nonces))
    second = create_jwt(credentials, "", nonce_factory=lambda: next(nonces))

    first_header = jwt.get_unverified_header(first)
    first_payload = jwt.decode(first, credentials.secret_key, algorithms=["HS512"])
    second_payload = jwt.decode(second, credentials.secret_key, algorithms=["HS512"])

    assert first_header["alg"] == "HS512"
    assert first_payload == {
        "access_key": "fake-access-key",
        "nonce": "nonce-1",
        "query_hash": query_hash("market=KRW-BTC"),
        "query_hash_alg": "SHA512",
    }
    assert second_payload == {"access_key": "fake-access-key", "nonce": "nonce-2"}


def test_credentials_load_from_environment_or_absolute_read_only_files(tmp_path: Path) -> None:
    assert load_credentials(
        {"UPBIT_ACCESS_KEY": "fake-access", "UPBIT_SECRET_KEY": "s" * 64}
    ) == Credentials("fake-access", "s" * 64)

    access_file = tmp_path / "access"
    secret_file = tmp_path / "secret"
    access_file.write_text("fake-file-access\n", encoding="utf-8")
    secret_file.write_text("t" * 64 + "\n", encoding="utf-8")
    os.chmod(access_file, 0o400)
    os.chmod(secret_file, 0o400)
    assert load_credentials(
        {"UPBIT_ACCESS_KEY_FILE": str(access_file), "UPBIT_SECRET_KEY_FILE": str(secret_file)}
    ) == Credentials("fake-file-access", "t" * 64)


def test_credentials_reject_partial_ambiguous_and_writable_file_sources(tmp_path: Path) -> None:
    with pytest.raises(CredentialConfigurationError):
        load_credentials({"UPBIT_ACCESS_KEY": "fake-access"})
    with pytest.raises(CredentialConfigurationError):
        load_credentials(
            {
                "UPBIT_ACCESS_KEY": "fake-access",
                "UPBIT_SECRET_KEY": "s" * 64,
                "UPBIT_ACCESS_KEY_FILE": "/tmp/not-read",
                "UPBIT_SECRET_KEY_FILE": "/tmp/not-read",
            }
        )
    writable = tmp_path / "writable"
    writable.write_text("fake", encoding="utf-8")
    with pytest.raises(CredentialConfigurationError):
        load_credentials(
            {"UPBIT_ACCESS_KEY_FILE": str(writable), "UPBIT_SECRET_KEY_FILE": str(writable)}
        )
