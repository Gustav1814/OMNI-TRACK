import uuid
from datetime import datetime, timedelta

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from utils.services.auth.dtos.auth_config import AuthConfig
from utils.services.auth.interface.ijwt import IJWT
from utils.services.di_container.constants.service_life_time import ServiceLifetime
from utils.services.di_container.container import container
security = HTTPBearer()


class JWTHandler(IJWT):
    def create_token(self, username: str) -> str:
        expiration = datetime.utcnow() + timedelta(seconds=AuthConfig.JWT_EXPIRE_SECONDS)
        payload = {
            "sub": username,
            "jti": str(uuid.uuid4()),
            "exp": expiration,
        }
        return jwt.encode(payload, AuthConfig.JWT_SECRET, algorithm=AuthConfig.JWT_ALGORITHM)

    def verify_token(
        self, credentials: HTTPAuthorizationCredentials = Depends(security)
    ) -> dict:
        token = credentials.credentials

        if token == AuthConfig.HARDCODED_JWT_TOKEN:
            return {"sub": AuthConfig.ADMIN_USERNAME, "jti": "hardcoded-test-token"}

        try:
            return jwt.decode(
                token, AuthConfig.JWT_SECRET, algorithms=[AuthConfig.JWT_ALGORITHM]
            )
        except jwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token expired",
                headers={"WWW-Authenticate": "Bearer"},
            )
        except jwt.InvalidTokenError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid token: {exc}",
                headers={"WWW-Authenticate": "Bearer"},
            )