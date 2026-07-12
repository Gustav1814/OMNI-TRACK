import logging

from dtos.common.constants.api_config import API_NAME
from utils.services.logger.ilog_handler import ILogHandler
from utils.services.logger.log_levels import LogLevel


class ConsoleLogHandler(ILogHandler):
    """
    SRP: sole responsibility is writing log entries to the console.
    """

    def __init__(self) -> None:
        self._logger = logging.getLogger(f"{API_NAME}_Logger")
        self._logger.setLevel(logging.DEBUG)

        if not self._logger.handlers:
            handler = logging.StreamHandler()
            handler.setLevel(logging.DEBUG)
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )
            )
            self._logger.addHandler(handler)

    async def emit(self, level: LogLevel, entry: dict) -> None:
        python_level = getattr(logging, level.value, logging.INFO)
        self._logger.log(python_level, entry["formatted_message"])

    def emit_sync(self, level: LogLevel, entry: dict) -> None:
        """Synchronous variant used during startup/shutdown."""
        python_level = getattr(logging, level.value, logging.INFO)
        self._logger.log(python_level, entry["formatted_message"])
