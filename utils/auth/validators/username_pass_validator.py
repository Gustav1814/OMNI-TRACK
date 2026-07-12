from fastapi import HTTPException, status
from utils.services.auth.dtos.auth_config import AuthConfig

class UsernameAndPasswordValidator:
    def validate(self, username: str, password: str) -> None:
        if username != AuthConfig.ADMIN_USERNAME or password != AuthConfig.ADMIN_PASSWORD:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password",
            )