from __future__ import annotations

import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class WebSocketSecuritySettings:
    operator_token: str
    allowed_origins: tuple[str, ...]
    trust_proxy_headers: bool = False

    @classmethod
    def from_environment(cls, environ: Mapping[str, str]) -> WebSocketSecuritySettings:
        operator_token = environ.get(
            "UPBIT_GATEWAY_OPERATOR_TOKEN",
            environ.get("GOODMONEYING_OPERATOR_TOKEN", "local-dev-token"),
        )
        allowed_origins = tuple(
            normalized
            for value in environ.get("UPBIT_GATEWAY_ALLOWED_ORIGINS", "").split(",")
            if (normalized := _normalize_origin(value.strip())) is not None
        )
        return cls(
            operator_token=operator_token,
            allowed_origins=allowed_origins,
            trust_proxy_headers=environ.get("UPBIT_GATEWAY_TRUST_PROXY_HEADERS") == "true",
        )

    def authorizes(self, headers: Mapping[str, str], *, websocket_scheme: str) -> bool:
        supplied_token = headers.get("x-operator-token", "")
        if not self.operator_token or not hmac.compare_digest(
            supplied_token.encode("utf-8"), self.operator_token.encode("utf-8")
        ):
            return False
        origin = _normalize_origin(headers.get("origin", ""))
        if origin is None:
            return False
        if origin in self.allowed_origins:
            return True
        forwarded_host = ""
        forwarded_proto = ""
        if self.trust_proxy_headers:
            forwarded_host = (
                headers.get("x-forwarded-host", "").split(",", maxsplit=1)[0].strip()
            )
            forwarded_proto = (
                headers.get("x-forwarded-proto", "").split(",", maxsplit=1)[0].strip()
            )
        host = forwarded_host or headers.get("host", "")
        direct_proto = "https" if websocket_scheme == "wss" else "http"
        proto = forwarded_proto or direct_proto
        return origin == _normalize_origin(f"{proto}://{host}")


def _normalize_origin(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        return None
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username:
        return None
    default_port = 443 if parsed.scheme == "https" else 80
    authority = parsed.hostname.lower()
    if port is not None and port != default_port:
        authority = f"{authority}:{port}"
    return f"{parsed.scheme}://{authority}"
