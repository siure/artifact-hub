from __future__ import annotations

import secrets
from pathlib import Path


class AuthenticationError(Exception):
    pass


def load_token(path: Path) -> str:
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"Unable to read publish token from {path}") from exc
    if len(token) < 32:
        raise RuntimeError(f"Publish token in {path} is missing or too short")
    return token


def require_bearer(authorization: str | None, expected_token: str) -> None:
    prefix = "Bearer "
    if not authorization or not authorization.startswith(prefix):
        raise AuthenticationError
    supplied = authorization[len(prefix) :]
    if not secrets.compare_digest(supplied, expected_token):
        raise AuthenticationError
