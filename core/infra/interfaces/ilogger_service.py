from abc import ABC, abstractmethod
from typing import Any

from utils.services.logger.log_levels import LogLevel


class ILoggerService(ABC):
    """
    Public contract for the logger service.

    Consumers depend on this interface, never on LoggerService directly (DIP).
    """

    @abstractmethod
    async def log(self, level: LogLevel, message: str, **kwargs: Any) -> None:
        ...

    @abstractmethod
    async def info(self, message: str, **kwargs: Any) -> None:
        ...

    @abstractmethod
    async def error(self, message: str, **kwargs: Any) -> None:
        ...

    @abstractmethod
    async def warning(self, message: str, **kwargs: Any) -> None:
        ...

    @abstractmethod
    async def debug(self, message: str, **kwargs: Any) -> None:
        ...

    @abstractmethod
    async def critical(self, message: str, **kwargs: Any) -> None:
        ...

    @abstractmethod
    async def get_recent_logs(self) -> list[dict]:
        ...

    # Synchronous variants for non-async call sites (e.g. sync cache wrappers).
    @abstractmethod
    def sync_debug(self, message: str, **kwargs: Any) -> None:
        ...

    @abstractmethod
    def sync_info(self, message: str, **kwargs: Any) -> None:
        ...

    @abstractmethod
    def sync_warning(self, message: str, **kwargs: Any) -> None:
        ...

    @abstractmethod
    def sync_error(self, message: str, **kwargs: Any) -> None:
        ...
