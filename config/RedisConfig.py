from dataclasses import dataclass

from utils.common.custom_exceptions.environment_not_set import EnvironmentNotSet


@dataclass
class RedisConfig:
    AIS_REDIS_HOST: str
    AIS_REDIS_PORT: int
    AIS_REDIS_PASSWORD: str | None
    AIS_REDIS_LOGS_LIMIT: int

    def __init__(self, host=None, port=None, password=None, logs_limit=None):
        if host is None:
            raise EnvironmentNotSet("AIS_REDIS_HOST is required")
        if port is None:
            raise EnvironmentNotSet("AIS_REDIS_PORT is required")
        if password is None:
            raise EnvironmentNotSet("AIS_REDIS_PASSWORD is required")
        if logs_limit is None:
            raise EnvironmentNotSet("REDIS_LOGS_LIMIT is required")

        self.AIS_REDIS_HOST = host
        self.AIS_REDIS_PORT = int(port)
        self.AIS_REDIS_PASSWORD = password
        self.AIS_REDIS_LOGS_LIMIT = int(logs_limit)
