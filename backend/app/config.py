"""
OmniTrack AI — Application Configuration
Pydantic Settings: auto-loads from .env file

ALL CONFIGURATION LIVES HERE.
Change values by creating .env file in /backend/ (never hardcode secrets!)
"""

from pydantic_settings import BaseSettings
from typing import List, Optional
import json


class Settings(BaseSettings):
    # --- App ---
    APP_NAME: str = "OmniTrack AI"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # --- Database ---
    DATABASE_URL: str = "postgresql+asyncpg://omnitrack:omnitrack_secret@localhost:5432/omnitrack_db"

    # --- JWT ---
    JWT_SECRET_KEY: str = "change-this-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # --- AES-256 ---
    AES_SECRET_KEY: str = "0123456789abcdef0123456789abcdef"

    # --- Redis ---
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- AI Models ---
    YOLO_MODEL: str = "yolov8n.pt"           # YOLOv8 person detection model
    FIRE_MODEL: str = "yolov8n.pt"           # Fire/smoke detection model (you can train custom)
    REID_MODEL: str = "osnet_x1_0"           # Re-ID model (from torchreid)
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
    PROCESSING_FPS: int = 15                 # Frames/sec to process per camera
    MAX_CAMERAS: int = 16                    # Maximum simultaneous cameras
    FRAME_BUFFER_SIZE: int = 2               # Keep low = lower latency
    DEFAULT_SKIP_FRAMES: int = 1             # Process every Nth frame

    # --- CORS ---
    CORS_ORIGINS: List[str] = ["http://localhost:5173", "http://localhost:3000"]

    # --- Export ---
    EXPORT_DIR: str = "exports"              # Where CSV/JSON exports are saved

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


settings = Settings()
