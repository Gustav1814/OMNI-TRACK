# common/constants.py
from config.BaseConfig import config

API_NAME = config.CONTAINER_NAME
API_VERSION = "v1"
REDIS_JOB_PREFIX = "job:"
REDIS_RESULT_PREFIX = "result:"
REDIS_LOG_PREFIX = "log:"
REDIS_KPI_PREFIX = "KPI:"
MAX_RECONNECT_ATTEMPTS = 5
RECONNECT_DELAY = 5  # seconds
MAX_LOG_ENTRIES = 100
