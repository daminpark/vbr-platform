"""PIN-based authentication with signed cookies and rate limiting."""

import hashlib
import hmac
import time
from collections import defaultdict

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.config import settings

COOKIE_NAME = "vbr_session"
COOKIE_MAX_AGE = 30 * 24 * 3600  # 30 days

# Rate limiting: max 5 failed attempts per IP, then locked for 15 minutes
MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 15 * 60
_failed_attempts: dict[str, list[float]] = defaultdict(list)

# Paths that don't require authentication
PUBLIC_PATHS = {"/api/health", "/api/auth/login", "/api/auth/check"}
PUBLIC_PREFIXES = ("/api/webhooks/",)


def check_rate_limit(ip: str) -> int | None:
    """Check if IP is rate-limited. Returns seconds until unlock, or None if OK."""
    now = time.time()
    # Clean old attempts
    _failed_attempts[ip] = [t for t in _failed_attempts[ip] if now - t < LOCKOUT_SECONDS]
    if len(_failed_attempts[ip]) >= MAX_ATTEMPTS:
        oldest = _failed_attempts[ip][0]
        return int(LOCKOUT_SECONDS - (now - oldest))
    return None


def record_failed_attempt(ip: str):
    """Record a failed login attempt."""
    _failed_attempts[ip].append(time.time())


def clear_attempts(ip: str):
    """Clear failed attempts on successful login."""
    _failed_attempts.pop(ip, None)


def _sign(value: str) -> str:
    """Create HMAC signature for a cookie value."""
    return hmac.new(
        settings.secret_key.encode(), value.encode(), hashlib.sha256
    ).hexdigest()[:16]


def create_session_cookie(role: str) -> str:
    """Create a signed session value: role:timestamp:signature."""
    ts = str(int(time.time()))
    payload = f"{role}:{ts}"
    sig = _sign(payload)
    return f"{payload}:{sig}"


def verify_session_cookie(cookie: str) -> str | None:
    """Verify a signed session cookie. Returns role or None."""
    parts = cookie.split(":")
    if len(parts) != 3:
        return None
    role, ts, sig = parts
    if role not in ("owner", "cleaner"):
        return None
    expected = _sign(f"{role}:{ts}")
    if not hmac.compare_digest(sig, expected):
        return None
    # Check expiry
    try:
        created = int(ts)
    except ValueError:
        return None
    if time.time() - created > COOKIE_MAX_AGE:
        return None
    return role


class AuthMiddleware(BaseHTTPMiddleware):
    """Require valid session cookie for protected API routes."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Static files and frontend — no auth needed
        if not path.startswith("/api/"):
            return await call_next(request)

        # Public API paths
        if path in PUBLIC_PATHS:
            return await call_next(request)
        for prefix in PUBLIC_PREFIXES:
            if path.startswith(prefix):
                return await call_next(request)

        # Check session cookie
        cookie = request.cookies.get(COOKIE_NAME)
        if not cookie:
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)

        role = verify_session_cookie(cookie)
        if not role:
            return JSONResponse({"detail": "Session expired"}, status_code=401)

        # Attach role to request state
        request.state.role = role
        return await call_next(request)
