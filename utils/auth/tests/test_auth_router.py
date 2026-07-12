"""
Tests for AuthRouter (token endpoint wiring).

All cases follow Arrange–Act–Assert.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from utils.services.auth.dtos.auth_config import AuthConfig
from utils.services.auth.rest_api.auth_router import AuthRouter, TokenRequest


@pytest.mark.asyncio
async def test_generate_token_returns_bearer_payload_on_success() -> None:
    # Arrange
    fake_logger = AsyncMock()
    username = "router-user"
    password = "router-pass"
    jwt_secret = "router-jwt-secret-hmac-key-32b!!"
    expires_in = 900

    with patch.object(AuthConfig, "ADMIN_USERNAME", username), patch.object(
        AuthConfig, "ADMIN_PASSWORD", password
    ), patch.object(AuthConfig, "JWT_SECRET", jwt_secret), patch.object(
        AuthConfig, "JWT_ALGORITHM", "HS256"
    ), patch.object(
        AuthConfig, "JWT_EXPIRE_SECONDS", expires_in
    ), patch(
        "utils.services.auth.rest_api.auth_router.logger", fake_logger
    ):
        router = AuthRouter()
        request = TokenRequest(username=username, password=password)

        # Act
        response = await router.generate_token(request)

        # Assert
        assert response.token_type == "bearer"
        assert response.expires_in == expires_in
        assert isinstance(response.access_token, str) and response.access_token
        fake_logger.info.assert_awaited_once_with(
            "Token generated", username=username
        )


@pytest.mark.asyncio
async def test_generate_token_raises_401_when_credentials_invalid() -> None:
    # Arrange
    fake_logger = AsyncMock()

    with patch.object(AuthConfig, "ADMIN_USERNAME", "ok-user"), patch.object(
        AuthConfig, "ADMIN_PASSWORD", "ok-pass"
    ), patch("utils.services.auth.rest_api.auth_router.logger", fake_logger):
        router = AuthRouter()
        request = TokenRequest(username="ok-user", password="wrong")

        # Act / Assert
        with pytest.raises(HTTPException) as exc_info:
            await router.generate_token(request)

    assert exc_info.value.status_code == 401
    fake_logger.info.assert_not_called()


def test_auth_router_exposes_post_route_for_generate_token() -> None:
    # Arrange
    router = AuthRouter()

    # Act
    post_routes = [
        r
        for r in router.router.routes
        if getattr(r, "methods", None) and "POST" in r.methods
    ]
    paths = [getattr(r, "path", "") for r in post_routes]

    # Assert
    assert any("generate_token" in p for p in paths)