from abc import ABC, abstractmethod

from utils.services.logger.log_levels import LogLevel


class ILogHandler(ABC):
    """
    Abstract log handler.

    Follows OCP: new destinations (file, Sentry, etc.) are added by
    implementing this interface, not by editing LoggerService.
    Follows DIP: LoggerService depends on this abstraction, not concretions.
    """

    @abstractmethod
    async def emit(self, level: LogLevel, entry: dict) -> None:
        """Persist or forward a structured log entry."""
        ...
