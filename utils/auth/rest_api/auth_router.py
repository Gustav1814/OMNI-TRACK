from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from utils.services.auth.handlers.basic_auth_handler import JWTHandler
from utils.services.auth.validators.username_pass_validator import UsernameAndPasswordValidator
from utils.services.auth.dtos.auth_config import AuthConfig
from utils.helpers.logger_helpers import get_logger_service
from utils.services.logger.interfaces.ilogger_service import ILoggerService

logger: ILoggerService = get_logger_service()


class TokenRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


_jwt = JWTHandler()
_validator = UsernameAndPasswordValidator()


class AuthRouter:
    def __init__(self):
        self.name = "auth"
        self.router = APIRouter(prefix=f"/{self.name}")
        self.router.add_api_route(
            f"/{self.generate_token.__name__}",
            self.generate_token,
            methods=["POST"],
            response_model=TokenResponse,        # ✅ Swagger shows response shape
            summary="Generate JWT token",        # ✅ Swagger endpoint title
            description="Authenticate with username and password to receive a Bearer token.",  # ✅ Swagger description
            responses={
                200: {"description": "Token generated successfully"},
                401: {"description": "Invalid username or password"},
            },
        )

    async def generate_token(self, request: TokenRequest) -> TokenResponse:  # ✅ type hint shows request body
        """
        Authenticate and receive a JWT Bearer token.

        - **username**: admin username
        - **password**: admin password
        """
        _validator.validate(request.username, request.password)
        token = _jwt.create_token(request.username)
        await logger.info("Token generated", username=request.username)
        return TokenResponse(access_token=token, expires_in=AuthConfig.JWT_EXPIRE_SECONDS)