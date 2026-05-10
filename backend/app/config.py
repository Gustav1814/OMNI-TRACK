"""
OmniTrack AI — Application Configuration
Pydantic Settings: auto-loads from .env file

ALL CONFIGURATION LIVES HERE.
Change values by creating .env file in /backend/ (never hardcode secrets!)
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings


def _backend_dir() -> Path:
    """Directory containing `app/` (the FastAPI package root)."""
    return Path(__file__).resolve().parent.parent


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

    # --- Runtime profile ---
    # laptop: conservative defaults for CPU-only boxes
    # workstation: aggressive defaults for CUDA workstations
    RUNTIME_PROFILE: str = "laptop"

    # --- AI Models ---
    MODEL_WEIGHTS_DIR: str = "model_weight"  # Directory containing YOLO .pt files
    DEFAULT_YOLO_MODEL: str = "yolo11n.pt"   # Default model if none selected
    YOLO_MODEL: str = "yolo11n.pt"           # YOLO person detection model (legacy)
    FIRE_MODEL: str = "yolo11n.pt"           # Fire/smoke detection model (you can train custom)
    REID_MODEL: str = "osnet_x0_25"          # Re-ID model (from torchreid)
    REID_SIMILARITY_THRESHOLD: float = 0.6   # Cosine similarity threshold (higher = stricter; use 0.65–0.75 if many similar-looking people)
    REID_EMBEDDINGS_PER_ID: int = 5          # Max embeddings per global_id for multi-view (back/front/side) when face not visible
    DETECTION_CONFIDENCE: float = 0.5        # Min confidence to count a detection
    NMS_THRESHOLD: float = 0.45              # Non-max suppression (reduces duplicate boxes)
    DEVICE: str = "auto"                     # "auto", "cpu", "cuda", "mps" (for Apple M-series)

    # Old aliases for backward compatibility
    YOLO_MODEL_PATH: str = "yolo11n.pt"
    YOLO_CONFIDENCE: float = 0.5
    YOLO_NMS_THRESHOLD: float = 0.45
    FIRE_MODEL_PATH: str = "fire_smoke.pt"
    REID_MODEL_NAME: str = "osnet_x0_25"
    REID_BACKEND: str = "torchreid"          # torchreid | fastreid
    REID_WEIGHTS: str = ""                   # Optional FastReID checkpoint path
    SAM2_WEIGHTS: str = "sam2_b.pt"
    ENABLE_SAM2: bool = False                # Keep disabled by default on laptop

    # --- Tracking ---
    TRACKER_DEFAULT: str = "botsort.yaml"    # botsort.yaml | bytetrack.yaml or custom yaml path
    TRACKER_REID: bool = False

    # --- Pipeline ---
    PROCESSING_FPS: int = 15                 # Frames/sec to process per camera
    MAX_CAMERAS: int = 16                    # Maximum simultaneous cameras
    FRAME_BUFFER_SIZE: int = 2               # Keep low = lower latency
    DEFAULT_SKIP_FRAMES: int = 1             # Process every Nth frame
    DECODE_IMGSZ: int = 0                    # Optional decode-time resize (0 = disabled)

    # --- Pluggable backends ---
    EVENT_BUS_BACKEND: str = "redis"         # redis | kafka
    VECTOR_STORE_BACKEND: str = "faiss"      # faiss | pgvector | qdrant
    ENABLE_MEDIAPIPE: bool = True

    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_API_KEY: str = ""
    QDRANT_COLLECTION_PREFIX: str = "omnitrack_embeddings"

    # --- Resource guards ---
    MEMORY_GUARD_MB: int = 0                 # 0 => auto from available RAM * 0.85
    STORAGE_FREE_MIN_MB: int = 1024
    FOOTAGE_RETENTION_GB: int = 20

    # --- CORS ---
    CORS_ORIGINS: List[str] = ["http://localhost:5173", "http://localhost:3000"]

    # --- Export ---
    EXPORT_DIR: str = "exports"              # Where CSV/JSON exports are saved

    # --- CCTV Footage storage ---
    FOOTAGE_DIR: str = "storage/footage"      # Where uploaded/recorded clips are stored

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True

    @model_validator(mode="after")
    def apply_runtime_profile_defaults(self) -> "Settings":
        profile = (self.RUNTIME_PROFILE or "laptop").strip().lower()
        object.__setattr__(self, "RUNTIME_PROFILE", profile)

        # Apply defaults only if user didn't explicitly override env values.
        # We intentionally keep model choice open so users can pick any weight.
        if profile == "laptop":
            if self.PROCESSING_FPS == 15:
                object.__setattr__(self, "PROCESSING_FPS", 8)
            if self.MAX_CAMERAS == 16:
                object.__setattr__(self, "MAX_CAMERAS", 2)
            if self.DECODE_IMGSZ == 0:
                object.__setattr__(self, "DECODE_IMGSZ", 480)
            if self.TRACKER_REID:
                object.__setattr__(self, "TRACKER_REID", False)
            if self.ENABLE_SAM2:
                object.__setattr__(self, "ENABLE_SAM2", False)
        elif profile == "workstation":
            if self.PROCESSING_FPS == 15:
                object.__setattr__(self, "PROCESSING_FPS", 15)
            if self.MAX_CAMERAS == 16:
                object.__setattr__(self, "MAX_CAMERAS", 4)
            if self.DECODE_IMGSZ == 0:
                object.__setattr__(self, "DECODE_IMGSZ", 640)
            if self.DEFAULT_YOLO_MODEL in {"yolo11n.pt", "yolov8n.pt"}:
                object.__setattr__(self, "DEFAULT_YOLO_MODEL", "yolo26m.pt")
            if not self.ENABLE_SAM2:
                object.__setattr__(self, "ENABLE_SAM2", True)
            if not self.TRACKER_REID:
                object.__setattr__(self, "TRACKER_REID", True)

        # Keep a sane default tracker
        if not self.TRACKER_DEFAULT:
            object.__setattr__(self, "TRACKER_DEFAULT", "botsort.yaml")
        return self

    @model_validator(mode="after")
    def resolve_weight_paths(self) -> "Settings":
        """
        Turn bare filenames like yolov8n.pt into real paths under MODEL_WEIGHTS_DIR
        (or backend/) when the file exists there. Ultralytics can auto-download only
        when invoked from certain cwd; this makes local weights predictable.
        """
        backend = _backend_dir()
        weights_dir = (backend / self.MODEL_WEIGHTS_DIR).resolve()

        def resolve(ref: str) -> str:
            if not ref:
                return ref
            p = Path(ref)
            if p.is_file():
                return str(p.resolve())
            name = p.name
            for candidate in (
                backend / ref,
                weights_dir / name,
                Path(ref),
            ):
                try:
                    if candidate.is_file():
                        return str(candidate.resolve())
                except OSError:
                    continue
            return ref

        object.__setattr__(self, "YOLO_MODEL", resolve(self.YOLO_MODEL))
        object.__setattr__(self, "YOLO_MODEL_PATH", resolve(self.YOLO_MODEL_PATH))
        object.__setattr__(self, "FIRE_MODEL_PATH", resolve(self.FIRE_MODEL_PATH))
        object.__setattr__(self, "REID_WEIGHTS", resolve(self.REID_WEIGHTS))
        object.__setattr__(self, "SAM2_WEIGHTS", resolve(self.SAM2_WEIGHTS))
        return self


settings = Settings()
