import os
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

security = HTTPBasic()


def verify_admin_credentials(username: str, password: str) -> bool:
    expected_user = os.getenv("ADMIN_USER", "admin")
    expected_password = os.getenv("ADMIN_PASSWORD", "change-me")
    return secrets.compare_digest(username, expected_user) and secrets.compare_digest(
        password, expected_password
    )


def require_admin(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    if not verify_admin_credentials(credentials.username, credentials.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username
