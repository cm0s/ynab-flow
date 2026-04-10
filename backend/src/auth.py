import os
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")

# When no JWT secret is configured, make the bearer token optional (local dev / tests)
security = HTTPBearer(auto_error=bool(SUPABASE_JWT_SECRET))


def verify_token(credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> str:
    """Validate a Supabase JWT and return the user ID (sub claim)."""
    if not SUPABASE_JWT_SECRET:
        # Auth disabled (local dev without Supabase)
        return "dev-user"

    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing authorization header")

    from jose import jwt, JWTError

    try:
        payload = jwt.decode(
            credentials.credentials,
            SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            options={"verify_aud": False},
        )
        sub = payload.get("sub")
        if not sub:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: missing sub")
        return sub
    except JWTError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {e}")
