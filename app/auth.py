import logging
import os
import secrets
import time

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

logger = logging.getLogger(__name__)

security = HTTPBasic()

# Login rate limiting
_login_attempts: dict[str, list[float]] = {}
LOGIN_RATE_LIMIT = 5  # per minute
MAX_BUCKET_SIZE = 1000


def verify_admin_credentials(username: str, password: str) -> bool:
    expected_user = os.getenv("ADMIN_USER", "admin")
    expected_password = os.getenv("ADMIN_PASSWORD", "change-me")
    return secrets.compare_digest(username, expected_user) and secrets.compare_digest(
        password, expected_password
    )


def check_login_rate_limit(ip: str) -> None:
    """Prevent brute-force attacks on the login endpoint."""
    now = time.time()
    window_start = now - 60
    entries = [t for t in _login_attempts.get(ip, []) if t > window_start]
    if len(entries) >= LOGIN_RATE_LIMIT:
        logger.warning("Login rate limit exceeded for IP %s", ip)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Please try again later.",
        )
    entries.append(now)
    _login_attempts[ip] = entries

    # Bounded cleanup
    if len(_login_attempts) > MAX_BUCKET_SIZE:
        _cleanup_bucket(_login_attempts, window_start)


def _cleanup_bucket(bucket: dict[str, list[float]], window_start: float) -> None:
    stale_keys = [k for k, v in bucket.items() if not any(t > window_start for t in v)]
    for k in stale_keys:
        del bucket[k]


def verify_api_key(request: Request) -> bool:
    """Check X-API-Key header against configured API_KEY."""
    api_key = os.getenv("API_KEY", "")
    if not api_key:
        return False
    provided = request.headers.get("X-API-Key", "")
    return secrets.compare_digest(provided, api_key)


def require_admin(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    if not verify_admin_credentials(credentials.username, credentials.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def require_auth(request: Request) -> str:
    """Authenticate via API key or HTTP Basic credentials."""
    if verify_api_key(request):
        return "api-key"
    # Fall back to basic auth
    return require_admin()
