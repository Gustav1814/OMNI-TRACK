"""Initialize DI before auth modules that pull in JWTHandler."""

from __future__ import annotations

import utils.services.di_container.container  # noqa: F401
