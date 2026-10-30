"""Signed principal: who a run acts for, as the gateway vouches for it.

The gateway signs the user (email, name, groups, clearance, the specialist agents they may use) into a short-lived HS256 token
with PRINCIPAL_SIGNING_KEY, a secret it shares with the agent systems. When the key is set
(deployment), a run takes its principal only from a valid token, so another container on the
internal network cannot make up a user; a calling system forwards the token over A2A unchanged.
Without the key (local demo UIs, tests) the plain `principal` in the request body is used.

Standard library only: HS256 is HMAC-SHA256 over base64url(header).base64url(claims).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

from .schemas import Principal

ISSUER = "navitech-gateway"
AUDIENCE = "navitech-agents"
LEEWAY_SECONDS = 30


class PrincipalRejected(ValueError):
    """The request names a user, but not with a token this system can verify."""


def signing_key() -> str:
    return os.environ.get("PRINCIPAL_SIGNING_KEY", "").strip()


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(key: str, signing_input: str) -> bytes:
    return hmac.new(key.encode(), signing_input.encode(), hashlib.sha256).digest()


def sign(principal: Principal, key: str, *, ttl_seconds: int = 1800, now: float | None = None) -> str:
    """The token the gateway sends (the gateway has its own implementation; this one serves tests)."""
    issued = int(now if now is not None else time.time())
    claims = {"iss": ISSUER, "aud": AUDIENCE, "iat": issued, "exp": issued + ttl_seconds, "sub": principal.email,
              "name": principal.name, "groups": list(principal.groups), "clearance": principal.clearance}
    if principal.agents is not None:
        claims["agents"] = list(principal.agents)
    header = _b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64encode(json.dumps(claims, ensure_ascii=False).encode())
    return f"{header}.{payload}.{_b64encode(_mac(key, f'{header}.{payload}'))}"


def verify(token: str, key: str, *, now: float | None = None) -> Principal:
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
        header = json.loads(_b64decode(header_b64))
        claims: dict[str, Any] = json.loads(_b64decode(payload_b64))
        signature = _b64decode(signature_b64)
    except ValueError as e:  # wrong shape, bad base64 or JSON (json.JSONDecodeError is a ValueError)
        raise PrincipalRejected("principal_token 형식이 올바르지 않습니다.") from e
    if header.get("alg") != "HS256":
        raise PrincipalRejected("principal_token 알고리즘은 HS256이어야 합니다.")
    if not hmac.compare_digest(signature, _mac(key, f"{header_b64}.{payload_b64}")):
        raise PrincipalRejected("principal_token 서명이 올바르지 않습니다.")
    current = now if now is not None else time.time()
    if claims.get("iss") != ISSUER or claims.get("aud") != AUDIENCE:
        raise PrincipalRejected("principal_token 발급자 · 대상이 올바르지 않습니다.")
    if not isinstance(claims.get("exp"), int) or claims["exp"] + LEEWAY_SECONDS < current:
        raise PrincipalRejected("principal_token이 만료되었습니다.")
    agents = claims.get("agents")
    try:
        return Principal(email=claims["sub"], name=claims.get("name") or "", groups=tuple(claims.get("groups") or ()),
                         clearance=claims["clearance"], agents=tuple(agents) if isinstance(agents, list) else None)
    except (KeyError, ValueError) as e:
        raise PrincipalRejected("principal_token의 사용자 정보가 올바르지 않습니다.") from e


def trusted_principal(principal: Principal | None, token: str | None) -> tuple[Principal | None, str | None]:
    """(principal, token) a run may act on. With a signing key, only the token counts: a request
    naming a user without a valid token is rejected; one naming nobody runs anonymously."""
    key = signing_key()
    if not key:
        return principal, token
    if not token:
        if principal is not None:
            raise PrincipalRejected("서명된 principal_token 없이 보낸 사용자 정보는 받지 않습니다.")
        return None, None
    return verify(token, key), token
