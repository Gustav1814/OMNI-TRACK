"""
Tests for UsernameAndPasswordValidator.

All cases follow Arrange–Act–Assert.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from unittest.mock import patch

from utils.services.auth.dtos.auth_config import AuthConfig
from utils.services.auth.validators.username_pass_validator import (
    UsernameAndPasswordValidator,
)


def test_validate_accepts_matching_admin_credentials() -> None:
    # Arrange
    validator = UsernameAndPasswordValidator()
    username = "test-admin"
    password = "test-secret"

    with patch.object(AuthConfig, "ADMIN_USERNAME", username), patch.object(
        AuthConfig, "ADMIN_PASSWORD", password
    ):
        # Act
        validator.validate(username, password)

    # Assert — no exception


def test_validate_raises_when_username_does_not_match() -> None:
    # Arrange
    validator = UsernameAndPasswordValidator()

    with patch.object(AuthConfig, "ADMIN_USERNAME", "expected-user"), patch.object(
        AuthConfig, "ADMIN_PASSWORD", "expected-pass"
    ):
        # Act / Assert
        with pytest.raises(HTTPException) as exc_info:
            validator.validate("other-user", "expected-pass")

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid username or password"


def test_validate_raises_when_password_does_not_match() -> None:
    # Arrange
    validator = UsernameAndPasswordValidator()

    with patch.object(AuthConfig, "ADMIN_USERNAME", "expected-user"), patch.object(
        AuthConfig, "ADMIN_PASSWORD", "expected-pass"
    ):
        # Act / Assert
        with pytest.raises(HTTPException) as exc_info:
            validator.validate("expected-user", "wrong-pass")

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid username or password"


def test_validate_raises_when_both_credentials_wrong() -> None:
    # Arrange
    validator = UsernameAndPasswordValidator()

    with patch.object(AuthConfig, "ADMIN_USERNAME", "a"), patch.object(
        AuthConfig, "ADMIN_PASSWORD", "b"
    ):
        # Act / Assert
        with pytest.raises(HTTPException) as exc_info:
            validator.validate("x", "y")

    assert exc_info.value.status_code == 401
