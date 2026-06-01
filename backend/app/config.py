"""
OmniTrack AI — Application Configuration
Pydantic Settings: auto-loads from .env file

ALL CONFIGURATION LIVES HERE.
Change values by creating .env file in /backend/ (never hardcode secrets!)
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )
    # --- App ---
    APP_NAME: str = "OmniTrack AI"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # --- Database ---
    DATABASE_URL: str = "postgresql+asyncpg://omnitrack:omnitrack_secret@localhost:5432/omnitrack_db"
    DB_POOL_SIZE: int = 8
    DB_MAX_OVERFLOW: int = 4
    DB_POOL_TIMEOUT: int = 30
    DB_POOL_RECYCLE: int = 1800  # seconds — recycle connections before PG idle timeout
    DB_STATEMENT_TIMEOUT_MS: int = 30000  # per-query cap (ms); 0 = disabled
    DB_RUN_MIGRATIONS_ON_STARTUP: bool = True
    DB_RUN_ANALYZE_ON_STARTUP: bool = True
    DB_RETENTION_ON_STARTUP: bool = True
    DB_CREATE_FUTURE_PARTITIONS_ON_STARTUP: bool = True
    DB_PARTITION_MONTHS_AHEAD: int = 3
    DETECTION_RETENTION_DAYS: int = 30
    EMBEDDING_RETENTION_DAYS: int = 90
    ANALYTICS_RETENTION_DAYS: int = 365
    REID_GALLERY_WARM_LIMIT: int = 2000
    PGVECTOR_HNSW_M: int = 16
    PGVECTOR_HNSW_EF_CONSTRUCTION: int = 64
    DB_STARTUP_MAX_RETRIES: int = 30
    DB_STARTUP_RETRY_SECONDS: float = 2.0
    REDIS_STARTUP_MAX_RETRIES: int = 15
    REQUIRE_DATABASE: bool = True
    REQUIRE_REDIS: bool = True

    # --- JWT ---
    JWT_SECRET_KEY: str = "change-this-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    FIRST_USER_ADMIN: bool = True            # Bootstrap: first registered user becomes admin

    # --- AES-256 ---
    AES_SECRET_KEY: str = "0123456789abcdef0123456789abcdef"

    # --- Redis ---
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- AI Models ---
    MODEL_WEIGHTS_DIR: str = "model_weight"  # Directory containing YOLO .pt files
    DEFAULT_YOLO_MODEL: str = "yolov8n.pt"   # Default model if none selected
    YOLO_MODEL: str = "yolov8n.pt"           # YOLOv8 person detection model (legacy)
    FIRE_MODEL: str = "yolov8n.pt"           # Fire/smoke detection model (you can train custom)
    REID_MODEL: str = "osnet_x1_0"           # Re-ID model (from torchreid)
    REID_SIMILARITY_THRESHOLD: float = 0.6   # Cosine similarity threshold (higher = stricter; use 0.65–0.75 if many similar-looking people)
    REID_EMBEDDINGS_PER_ID: int = 5          # Max embeddings per global_id for multi-view (back/front/side) when face not visible
    DETECTION_CONFIDENCE: float = 0.5        # Min confidence to count a detection
    NMS_THRESHOLD: float = 0.45              # Non-max suppression (reduces duplicate boxes)
    DEVICE: str = "auto"                     # "auto", "cpu", "cuda", "mps" (for Apple M-series)

    # Old aliases for backward compatibility
    YOLO_MODEL_PATH: str = "yolov8n.pt"
    YOLO_CONFIDENCE: float = 0.5
    YOLO_NMS_THRESHOLD: float = 0.45
    FIRE_MODEL_PATH: str = "fire_smoke.pt"
    REID_MODEL_NAME: str = "osnet_x1_0"

    # --- Pipeline ---
    PROCESSING_FPS: int = 6                  # Frames/sec to process per camera
    MAX_CAMERAS: int = 8                     # Maximum simultaneous cameras
    FRAME_BUFFER_SIZE: int = 2               # Keep low = lower latency
    DEFAULT_SKIP_FRAMES: int = 2             # Process every Nth frame
    PIPELINE_RESULT_QUEUE_MAXSIZE: int = 256 # Backpressure boundary for future worker queues
    INFERENCE_BATCH_SIZE: int = 2            # Target batch size for future batched inference workers
    MAX_CPU_WORKERS: int = 2                 # Cap CPU-bound side work to control cloud/edge cost
    MAX_PARALLEL_CAMERA_PROCESSORS: int = 2  # Camera frames processed concurrently per tick
    MAX_REID_DETECTIONS_PER_FRAME: int = 8   # Avoid ReID crops exploding on crowded frames
    HEAVY_ANALYTICS_INTERVAL: int = 3        # Fire/emotion every N processed frames per camera
    CALLBACK_TIMEOUT_MS: int = 750           # Keep slow DB/WS/plugin callbacks off the hot path

    # --- Plugins / Integrations ---
    # Python module names loaded at API startup. Example:
    # ENABLED_PLUGINS=["app_plugins.loss_prevention","app_plugins.loyalty_bridge"]
    ENABLED_PLUGINS: List[str] = []
    PLUGIN_REQUIRE_EXPLICIT_ENABLE: bool = True

    # --- CORS ---
    CORS_ORIGINS: List[str] = ["http://localhost:5173", "http://localhost:3000"]

    # --- Export ---
    EXPORT_DIR: str = "exports"              # Where CSV/JSON exports are saved

    # --- CCTV Footage storage ---
    FOOTAGE_DIR: str = "storage/footage"      # Where uploaded/recorded clips are stored
    MAX_UPLOAD_MB: int = 512                  # Hard API cap for uploaded footage


settings = Settings()
