import os
from typing import Any

from config.GrpcConfig import GrpcConfig
from config.LogConfig import LogConfig
from config.ModelWeightsConfig import ModelWeightsConfig
from config.NatsConfig import NatsConfig
from config.RedisConfig import RedisConfig
from config.WeaviateConfig import WeaviateConfig


class BaseConfig:
    # Container and App Settings
    CONTAINER_NAME: str = os.getenv("AIS_SYSTEM_CONTAINER_NAME", "ais1")
    ALLOWED_ORIGINS: str = os.getenv("AIS_SYSTEM_ALLOWED_ORIGINS", "*")
    PORT: int = int(os.getenv("AIS_SYSTEM_PORT", "8000"))
    # When True, a GUID is auto-generated if job_id is null in the register request.
    # When False, job_id is required and a missing job_id raises an error.
    IS_JOB_GUID_DEFAULT_ALLOWED: bool = os.getenv(
        "Is_JOB_GUID_DEFAULT_ALLOWED", "TRUE"
    ).lower() in ("true", "1", "yes")

    REDIS: RedisConfig = RedisConfig(
        host=os.getenv("AIS_REDIS_HOST", "localhost"),
        port=int(os.getenv("AIS_REDIS_PORT", "6379")),
        password=os.getenv("AIS_REDIS_PASSWORD", "Verseye!23"),
        logs_limit=os.getenv("AIS_REDIS_LOGS_LIMIT", "50"),
    )

    NATS: NatsConfig = NatsConfig(
        url=os.getenv("AIS_NATS_URL", "nats://localhost:4222"),
        user=os.getenv("AIS_NATS_User", None),
        password=os.getenv("AIS_NATS_Password", None),
        queue_maxsize=int(os.getenv("AIS_NATS_QUEUE_MAXSIZE", "1000")),
        num_workers=int(os.getenv("AIS_NATS_NUM_WORKERS", "4")),
        detections_subject=os.getenv("AIS_NATS_DETECTIONS_SUBJECT", "detections"),
        payload_buffer_size=int(os.getenv("AIS_NATS_PAYLOAD_BUFFER_SIZE", 100)),
    )

    MODEL_WEIGHTS: ModelWeightsConfig = ModelWeightsConfig(
        model_weights_json=os.getenv("AIS_MODEL_WEIGHTS_JSON", None)
    )

    LOGS: LogConfig = LogConfig(
        debug=os.getenv("AIS_LOG_DEBUG", "1"),
        info=os.getenv("AIS_LOG_INFO", "1"),
        warning=os.getenv("AIS_LOG_WARNING", "1"),
        error=os.getenv("AIS_LOG_ERROR", "1"),
        critical=os.getenv("AIS_LOG_CRITICAL", "1"),
    )

    GRPC: GrpcConfig = GrpcConfig(
        server=os.getenv("AIS_GRPC_SERVER", ""),
        jpeg_quality=os.getenv("AIS_JPEG_QUALITY", "95"),
        timeout=os.getenv("AIS_GRPC_TIMEOUT", "5"),
        face_embedding_grpc=os.getenv("AIS_FACE_EMBEDDING_GRPC", "163.61.91.33:30050"),
    )

    WEAVIATE: WeaviateConfig = WeaviateConfig(
        host=os.getenv("AIS_WEAVIATE_HOST", "localhost"),
        port=os.getenv("AIS_WEAVIATE_HTTP_PORT", "8080"),
        grpc_port=os.getenv("AIS_WEAVIATE_GRPC_PORT", "50051"),
    )

    FALLBACK_TRACKER: str = "bytetrack"
    TRACKER_CONFIG: dict[str, Any] = {}
    TRACKER_DEFAULTS: dict[str, dict[str, Any]] = {}

    from constants.kpi_names_constant import API_KPIS_LIST

    API_KPIS_LIST: dict[str, str] = API_KPIS_LIST  # type: ignore[no-redef]

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.load_tracker_defaults()
        return cls._instance

    def load_tracker_defaults(self) -> None:
        import os

        import yaml

        workspace_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        trackers_dir = os.path.join(workspace_root, "services", "model", "cfgs", "trackers")
        defaults = {}
        if os.path.exists(trackers_dir):
            for filename in os.listdir(trackers_dir):
                if filename.endswith(".yaml") and not filename.startswith("custom_tracker"):
                    name = filename[:-5]  # remove ".yaml"
                    path = os.path.join(trackers_dir, filename)
                    try:
                        with open(path) as f:
                            cfg = yaml.safe_load(f)
                            if isinstance(cfg, dict):
                                defaults[name] = cfg
                    except Exception:
                        pass
        self.TRACKER_DEFAULTS = defaults


config = BaseConfig()
