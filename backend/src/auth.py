"""Supabase authentication for the single-admin, shared YNAB database."""
import os
import time

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt, JWTError

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")
ADMIN_USER_ID = os.environ.get("ADMIN_USER_ID", "")
AUTH_REQUIRED = os.environ.get("AUTH_REQUIRED", "").lower() == "true" or bool(os.environ.get("RENDER"))
security = HTTPBearer(auto_error=False)
_jwks_cache: dict = {}
_jwks_expires = 0.0


def validate_auth_config():
    if AUTH_REQUIRED or SUPABASE_URL or SUPABASE_JWT_SECRET:
        if not SUPABASE_URL or not ADMIN_USER_ID:
            raise RuntimeError("Authentication requires SUPABASE_URL and ADMIN_USER_ID")


def _get_jwks():
    global _jwks_cache, _jwks_expires
    if time.monotonic() >= _jwks_expires:
        try:
            response = httpx.get(f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json", timeout=5)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get("keys"), list):
                raise ValueError("Invalid JWKS")
            _jwks_cache = data
            _jwks_expires = time.monotonic() + 300
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(status_code=503, detail="Authentication service unavailable") from exc
    return _jwks_cache


def verify_token(credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> str:
    if not (AUTH_REQUIRED or SUPABASE_URL or SUPABASE_JWT_SECRET):
        return "dev-user"
    validate_auth_config()
    if not credentials:
        raise HTTPException(status_code=401, detail="Missing authorization header", headers={"WWW-Authenticate": "Bearer"})
    try:
        algorithm = jwt.get_unverified_header(credentials.credentials).get("alg")
        if algorithm == "HS256" and SUPABASE_JWT_SECRET:
            key = SUPABASE_JWT_SECRET
        elif algorithm in ("ES256", "RS256"):
            key = _get_jwks()
        else:
            raise JWTError("Unsupported signing algorithm")
        payload = jwt.decode(
            credentials.credentials, key, algorithms=[algorithm],
            audience="authenticated", issuer=f"{SUPABASE_URL}/auth/v1",
            options={"require_exp": True, "require_sub": True, "require_aud": True, "require_iss": True},
        )
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired token", headers={"WWW-Authenticate": "Bearer"}) from exc
    if payload["sub"] != ADMIN_USER_ID or payload.get("role") != "authenticated":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return payload["sub"]
