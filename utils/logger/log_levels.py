from enum import StrEnum


class LogLevel(StrEnum):
    """
    Log severity levels.
    String enum so values can be used directly
    with Python's logging module (e.g. logging.getLevelName).
    """

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"
