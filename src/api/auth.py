"""
Minimal single-user site auth.

This whole project has exactly one real user (me), and this module exists
to gate one thing: the real-Upstox-account routes, so they're not sitting
wide open at a public URL. It is deliberately NOT a general-purpose auth
system -- no user table, no password-hashing library, no signup flow. One
shared password (constant-time compared) issues a signed, time-limited
token; every protected route just checks that token. Good enough for "only
I can see my own real holdings"; would not scale to a real multi-user
product without a proper user/password store.
"""
from __future__ import annotations

import os
import secrets

from dotenv import load_dotenv
from fastapi import Header, HTTPException
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

load_dotenv()

# Long enough that you're not re-logging-in mid-session, short enough that
# a token copied out of localStorage by mistake doesn't stay valid forever.
SESSION_MAX_AGE_SECONDS = 12 * 60 * 60


def _serializer() -> URLSafeTimedSerializer:
    secret = os.environ["SITE_SESSION_SECRET"]
    return URLSafeTimedSerializer(secret, salt="roundtable-site-session")


def check_password(password: str) -> bool:
    expected = os.environ["SITE_LOGIN_PASSWORD"]
    # secrets.compare_digest -- a plain `==` here would leak timing info
    # about how many leading characters matched. Overkill for a hobby
    # project's single password, but it's free and it's the honest way to
    # compare secrets.
    return secrets.compare_digest(password, expected)


def issue_session_token() -> str:
    return _serializer().dumps({"user": "uday"})


def verify_session_token(token: str) -> bool:
    try:
        _serializer().loads(token, max_age=SESSION_MAX_AGE_SECONDS)
        return True
    except (BadSignature, SignatureExpired):
        return False


async def require_session(authorization: str | None = Header(default=None)) -> None:
    """
    FastAPI dependency that protects a route behind a valid session token.
    Expects `Authorization: Bearer <token>`. Raises 401 if missing, malformed,
    invalid, or expired.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing session token")
    token = authorization.removeprefix("Bearer ")
    if not verify_session_token(token):
        raise HTTPException(status_code=401, detail="Invalid or expired session")
