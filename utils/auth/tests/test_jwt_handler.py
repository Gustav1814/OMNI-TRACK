"""
Tests for JWTHandler (token creation and verification).

All cases follow Arrange–Act–Assert.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from utils.services.auth.dtos.auth_config import AuthConfig
from utils.services.auth.handlers.basic_auth_handler import JWTHandler


def test_create_token_returns_decodable_jwt_with_expected_claims() -> None:
    # Arrange
    handler = JWTHandler()
    secret = "unit-test-secret-hmac-key-32bytes!!"
    algorithm = "HS256"
    expire_seconds = 120
    subject = "test-user"

    with patch.object(AuthConfig, "JWT_SECRET", secret), patch.object(
        AuthConfig, "JWT_ALGORITHM", algorithm
    ), patch.object(AuthConfig, "JWT_EXPIRE_SECONDS", expire_seconds):
        # Act
        token = handler.create_token(subject)

        # Assert
        decoded = jwt.decode(token, secret, algorithms=[algorithm])
        assert decoded["sub"] == subject
        assert "jti" in decoded and decoded["jti"]
        exp = decoded["exp"]
        assert isinstance(exp, int)
        assert datetime.utcfromtimestamp(exp) > datetime.utcnow()


def test_verify_token_accepts_valid_bearer_jwt() -> None:
    # Arrange
    handler = JWTHandler()
    secret = "verify-secret-hmac-key-32bytes!!!"
    algorithm = "HS256"
    username = "api-user"

    with patch.object(AuthConfig, "JWT_SECRET", secret), patch.object(
        AuthConfig, "JWT_ALGORITHM", algorithm
    ), patch.object(AuthConfig, "JWT_EXPIRE_SECONDS", 3600):
        token = handler.create_token(username)
        credentials = HTTPAuthorizationCredentials(
            scheme="Bearer", credentials=token
        )

        # Act
        payload = handler.verify_token(credentials=credentials)

        # Assert
        assert payload["sub"] == username


def test_verify_token_accepts_configured_hardcoded_token() -> None:
    # Arrange
    handler = JWTHandler()
    hardcoded = "hardcoded-test-jwt-value"
    admin_name = "hardcoded-admin"

    with patch.object(AuthConfig, "HARDCODED_JWT_TOKEN", hardcoded), patch.object(
        AuthConfig, "ADMIN_USERNAME", admin_name
    ):
        credentials = HTTPAuthorizationCredentials(
            scheme="Bearer", credentials=hardcoded
        )

        # Act
        payload = handler.verify_token(credentials=credentials)

        # Assert
        assert payload == {
            "sub": admin_name,
            "jti": "hardcoded-test-token",
        }


def test_verify_token_raises_when_signature_expired() -> None:
    # Arrange
    handler = JWTHandler()
    secret = "expiry-secret-hmac-key-32bytes!!!"
    algorithm = "HS256"
    past = datetime.utcnow() - timedelta(seconds=10)
    payload = {
        "sub": "u",
        "jti": "id",
        "exp": past,
    }
    token = jwt.encode(payload, secret, algorithm=algorithm)
    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer", credentials=token
    )

    with patch.object(AuthConfig, "JWT_SECRET", secret), patch.object(
        AuthConfig, "JWT_ALGORITHM", algorithm
    ):
        # Act / Assert
        with pytest.raises(HTTPException) as exc_info:
            handler.verify_token(credentials=credentials)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Token expired"


def test_verify_token_raises_when_token_is_invalid() -> None:
    # Arrange
    handler = JWTHandler()
    secret = "reject-secret-hmac-key-32bytes!!!"
    algorithm = "HS256"

    with patch.object(AuthConfig, "JWT_SECRET", secret), patch.object(
        AuthConfig, "JWT_ALGORITHM", algorithm
    ):
        credentials = HTTPAuthorizationCredentials(
            scheme="Bearer", credentials="not-a-jwt"
        )

        # Act / Assert
        with pytest.raises(HTTPException) as exc_info:
            handler.verify_token(credentials=credentials)

    assert exc_info.value.status_code == 401
    assert "Invalid token" in str(exc_info.value.detail)
