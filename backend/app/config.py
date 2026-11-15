"""
OmniTrack AI — Application Configuration
Pydantic Settings: auto-loads from .env file

ALL CONFIGURATION LIVES HERE.
Change values by creating .env file in /backend/ (never hardcode secrets!)
"""

from pathlib import Path
from typing import Dict, List

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ locally, /app in Docker. Relative weight paths resolve against this.
BACKEND_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_ROOT / ".env"),  # absolute: CWD-independent
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
    STORE_TIMEZONE: str = "UTC"              # IANA name; "today" and hour-of-day analytics use this zone
    REID_GALLERY_WARM_LIMIT: int = 2000
    PGVECTOR_HNSW_M: int = 16
    PGVECTOR_HNSW_EF_CONSTRUCTION: int = 64
    DB_STARTUP_MAX_RETRIES: int = 30
    DB_STARTUP_RETRY_SECONDS: float = 2.0
    REDIS_STARTUP_MAX_RETRIES: int = 15
    REQUIRE_DATABASE: bool = True
    REQUIRE_REDIS: bool = True

    # --- JWT ---
    # Secrets have NO usable defaults: startup refuses empty/placeholder/short
    # values (app/security/secrets_check.py). Generate them with
    # `python scripts/generate_secrets.py` which writes backend/.env.
    JWT_SECRET_KEY: str = ""
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    FIRST_USER_ADMIN: bool = True            # Bootstrap: first registered user becomes admin
    ALLOW_PUBLIC_REGISTRATION: bool = False  # After bootstrap, only admins create accounts
    ENFORCE_SECRET_STRENGTH: bool = True     # Refuse to start with weak/default secrets
    LOGIN_MAX_FAILURES: int = 5              # Consecutive failures before the account locks
    LOGIN_LOCKOUT_MINUTES: int = 15
    TRUSTED_PROXIES: List[str] = []          # IPs/CIDRs whose X-Forwarded-For is honoured
    ADMIN_SESSION_SECRET: str = ""           # SQLAdmin cookie key; derived from JWT secret if empty

    # --- AES-256-GCM (records at rest) ---
    AES_SECRET_KEY: str = ""                 # 64 hex chars / base64 of 32 bytes, or >=32-char passphrase (HKDF)

    # --- Audit chain (HMAC-SHA256) ---
    AUDIT_HMAC_KEY: str = ""
    AUDIT_VERIFY_INTERVAL_HOURS: float = 24.0
    AUDIT_ANCHOR_PATH: str = "storage/security/audit_anchor.json"

    # --- Input safety ---
    CAMERA_SOURCE_ALLOWLIST: List[str] = []  # Hosts/CIDRs allowed for http(s) camera sources
    ALLOW_WEBCAM_SOURCES: bool = True        # Numeric local device indexes ("0", "1")
    VIDEO_SOURCE_DIRS: List[str] = ["storage/footage", "storage/demo_videos", "storage/datasets"]

    # --- Redis ---
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- AI Models ---
    MODEL_WEIGHTS_DIR: str = "model_weight"  # Directory containing YOLO .pt files (relative = relative to backend/)
    DEFAULT_YOLO_MODEL: str = "yolov8n.pt"   # Default model if none selected (file name inside MODEL_WEIGHTS_DIR)
    YOLO_MODEL: str = "yolov8n.pt"           # Pipeline fallback person model; resolved to MODEL_WEIGHTS_DIR/<name>
    FIRE_MODEL: str = "fire-smoke.pt"        # Legacy alias; FIRE_MODEL_PATH is the one actually used
    REID_MODEL: str = "osnet_ain_x1_0"       # Re-ID architecture (torchreid); must match REID_WEIGHTS_PATH
    REID_WEIGHTS_PATH: str = "osnet_ain_x1_0_msmt17.pth"  # Re-ID-trained weights (MSMT17); ImageNet weights cannot tell people apart
    # Re-ID matching (tracklet = L2-normalised mean of the best REID_TRACKLET_TOP_K quality crops).
    # CAVIAR open-set study (osnet_ain_x1_0, 632 tracklets/52 people): at 0.75 returning people get their
    # id back 88% of the time (5% split) while ungated new-person false merges fall 95% -> 18% vs 0.55.
    REID_T_MATCH: float = 0.75                # Min cosine sim for a new tracklet to take an existing identity (Hungarian)
    REID_T_UPDATE: float = 0.85               # Min sim before a finished tracklet is added to its identity as a new view
    REID_SIMILARITY_THRESHOLD: float = 0.75   # Default threshold of the /api/reid/search endpoint (= REID_T_MATCH)
    REID_EMBEDDINGS_PER_ID: int = 8           # Max diverse views (front/back/side) kept per identity
    REID_TRACKLET_TOP_K: int = 5              # Crops averaged into a tracklet feature
    REID_TRACKLET_MIN_CROPS: int = 2          # Fewer than this never gets a global ID
    REID_TRACKLET_MAX_WAIT_S: float = 3.0     # Assign with >= MIN_CROPS after this long instead of waiting for TOP_K
    # Crop quality gate: only these crops are embedded.
    REID_MIN_CROP_HEIGHT: int = 128           # Min person height (px) ...
    REID_MIN_CROP_HEIGHT_FRAC: float = 0.25   # ... or this fraction of frame height, whichever is smaller (low-res cameras)
    REID_MIN_ASPECT: float = 0.25             # w/h range of a standing person
    REID_MAX_ASPECT: float = 0.6
    REID_EDGE_MARGIN_PX: int = 2              # Boxes touching the frame edge are partial
    REID_MAX_OCCLUSION_IOU: float = 0.3       # Max IoU with any other person box
    REID_MIN_DET_CONF: float = 0.6
    # Cross-camera gating. Topology JSON: [{"from":1,"to":2,"min_s":5,"max_s":120,"overlapping":false,"bidirectional":true}]
    # (also editable at /api/reid/topology). Empty = any camera pair within REID_DEFAULT_MAX_TRANSIT_S.
    REID_CAMERA_TOPOLOGY: str = ""
    REID_DEFAULT_MAX_TRANSIT_S: float = 300.0 # How long a disappeared identity can be re-matched on another camera
    REID_REENTRY_MAX_S: float = 1800.0        # How long it can be re-matched on the camera it left
    REID_HANDOFF_BONUS: float = 0.1           # Cost discount for an expected arrival (topology link window); never lowers T_MATCH
    REID_FUSION_DISTANCE_M: float = 0.75      # Calibrated overlapping cameras: same floor spot (m) for >= 1 s fuses IDs
    REID_GALLERY_MAX_IDENTITIES: int = 2000   # In-memory gallery holds recent identities only; pgvector holds the rest
    REID_WARM_START_ROWS: int = 4000          # Embedding rows restored into the gallery at startup
    # Person detector: one model, chosen by device unless PERSON_MODEL is set.
    PERSON_MODEL: str = ""                    # Explicit override (file name in MODEL_WEIGHTS_DIR)
    PERSON_MODEL_CPU: str = "yolo11s.pt"
    PERSON_MODEL_GPU: str = "yolo11m.pt"
    PERSON_IMGSZ_CPU: int = 640
    PERSON_IMGSZ_GPU: int = 960
    PERSON_CONF: float = 0.1                  # Low on purpose: ByteTrack's second stage uses the weak boxes
    PERSON_IOU: float = 0.6
    DETECTOR_EXPORT: str = "auto"             # auto (CPU: openvino>onnx, CUDA: engine) | none | openvino | onnx | engine; falls back to PyTorch
    # ByteTrack + track lifecycle
    TRACK_HIGH_THRESH: float = 0.5
    TRACK_LOW_THRESH: float = 0.1
    TRACK_NEW_THRESH: float = 0.6
    TRACK_MATCH_THRESH: float = 0.8
    TRACK_BUFFER_S: float = 3.0               # Lost tracks are kept this long (converted to frames at the real FPS)
    TRACK_MIN_HITS: int = 3                   # Tentative -> confirmed
    DETECTION_CONFIDENCE: float = 0.5        # Min confidence to count a detection
    NMS_THRESHOLD: float = 0.45              # Non-max suppression (reduces duplicate boxes)
    DEVICE: str = "auto"                     # "auto", "cpu", "cuda", "mps" (for Apple M-series)
    ALLOW_MOCK_AI: bool = False              # Never true outside unit tests: a model that cannot load logs an ERROR and returns no output

    FIRE_MODEL_PATH: str = "fire-smoke.pt"   # Resolved to MODEL_WEIGHTS_DIR/<name> unless absolute
    REID_MODEL_NAME: str = "osnet_x1_0"
    FACE_MODEL_PATH: str = "face11n.pt"      # YOLO face detector feeding the emotion classifier
    EMOTION_MODEL_PATH: str = "enet_b0_8_best_afew.onnx"  # HSEmotion EfficientNet-B0, AffectNet 8 classes (ONNX)
    EMOTION_MIN_FACE_PX: int = 40            # Smaller faces are skipped: too few pixels for a meaningful reading
    EMOTION_MAX_FACES_PER_FRAME: int = 8     # Largest faces first; caps CPU cost on crowded frames
    EMOTION_THREADS: int = 2                 # ONNX Runtime intra-op threads (leave cores for YOLO/Re-ID)
    # Age band / gender on the emotion module's face crops (FairFace ResNet-34, CC BY 4.0, exported to ONNX
    # by scripts/download_default_weights.py). Missing file => demographics disabled ("model_not_installed").
    DEMOGRAPHICS_MODEL_PATH: str = "fairface_res34.onnx"
    DEMOGRAPHICS_MIN_FACE_SAMPLES: int = 2   # face readings a visit needs before it gets an age band / gender
    DEMOGRAPHICS_MAX_SAMPLES_PER_VISIT: int = 10  # stop classifying a track's face after this many readings
    DEMOGRAPHICS_MIN_VISITS: int = 20        # API hides percentages below this many attributed visits

    # --- Pipeline ---
    PROCESSING_FPS: int = 6                  # Frames/sec to process per camera
    MAX_CAMERAS: int = 8                     # Maximum simultaneous cameras
    FRAME_BUFFER_SIZE: int = 2               # Keep low = lower latency
    DEFAULT_SKIP_FRAMES: int = 0             # Legacy API field; frames are decimated by PROCESSING_FPS only
    MAX_REID_DETECTIONS_PER_FRAME: int = 8   # Re-ID crops embedded per frame (best quality first)
    AUTO_CAMERA_ROLE_DETECTION: bool = True   # Infer per-camera role and module policy from zone/source/activity
    EMOTION_ANALYTICS_INTERVAL_FRAMES: int = 12 # Emotion cadence; never runs on empty frames
    CHECKOUT_ZONE_KEYWORDS: str = "checkout,counter,cashier,billing,pos"
    SHELF_ZONE_KEYWORDS: str = "shelf,aisle,rack,product,fridge,cooler"
    ENTRANCE_ZONE_KEYWORDS: str = "entrance,entry,exit,door,gate"
    SAFETY_ZONE_KEYWORDS: str = "kitchen,storage,warehouse,electrical,stockroom"
    CALLBACK_TIMEOUT_MS: int = 750           # Keep slow DB/WS/plugin callbacks off the hot path
    FIRE_INTERVAL_FRAMES: int = 10           # Fire/smoke model every N processed frames per camera
    PRODUCT_MODEL_PATH: str = "general_product_detection.pt"
    PRODUCT_INTERVAL_S: float = 2.0          # Product/shelf model cadence per camera (0 = off)
    PIPELINE_RENDER_THREADS: int = 2         # Annotate + JPEG-encode for viewers off the event loop
    MEMORY_PRESSURE_PCT: float = 90.0        # Above this system RAM use, lower FPS (models are never unloaded)

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

    # --- Alert evidence (snapshot + short clip per alert, AES-GCM encrypted at rest) ---
    EVIDENCE_ENABLED: bool = True
    EVIDENCE_DIR: str = "storage/evidence"
    EVIDENCE_RETENTION_DAYS: int = 30         # files + rows older than this are purged
    EVIDENCE_MAX_GB: float = 5.0              # quota: oldest evidence is evicted beyond this
    EVIDENCE_PRE_S: float = 4.0               # clip seconds before the alert
    EVIDENCE_POST_S: float = 5.0              # clip seconds after the alert (capture waits for it)
    EVIDENCE_QUEUE_SIZE: int = 32             # pending captures; extra captures are dropped + logged
    # Per-camera pre-roll ring for evidence (JPEG frames, downscaled); holds PRE_S + POST_S + slack seconds.
    EVIDENCE_RING_FPS: float = 8.0            # frames/s kept per camera
    EVIDENCE_RING_MAX_WIDTH: int = 960        # frames are downscaled to at most this width
    EVIDENCE_RING_TOTAL_MB: int = 150         # RAM budget shared by all cameras' rings

    @field_validator("DEBUG", mode="before")
    @classmethod
    def parse_debug_mode(cls, value):
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"release", "prod", "production", "off", "false", "0", "no"}:
                return False
            if normalized in {"debug", "dev", "development", "on", "true", "1", "yes"}:
                return True
        return value

    @model_validator(mode="after")
    def resolve_weight_paths(self):
        """Make MODEL_WEIGHTS_DIR absolute and resolve file-path weights inside it.

        DEFAULT_YOLO_MODEL stays a bare file name because the API compares it
        against the names listed in MODEL_WEIGHTS_DIR.
        """
        weights_dir = Path(self.MODEL_WEIGHTS_DIR).expanduser()
        if not weights_dir.is_absolute():
            weights_dir = BACKEND_ROOT / weights_dir
        self.MODEL_WEIGHTS_DIR = str(weights_dir.resolve())
        for attr in ("FOOTAGE_DIR", "EXPORT_DIR", "AUDIT_ANCHOR_PATH", "EVIDENCE_DIR"):
            p = Path(getattr(self, attr)).expanduser()
            setattr(self, attr, str(p if p.is_absolute() else BACKEND_ROOT / p))
        self.VIDEO_SOURCE_DIRS = [
            str(Path(d).expanduser() if Path(d).expanduser().is_absolute() else BACKEND_ROOT / d)
            for d in self.VIDEO_SOURCE_DIRS
        ]
        self.YOLO_MODEL = str(self.weight_path(self.YOLO_MODEL))
        self.FIRE_MODEL_PATH = str(self.weight_path(self.FIRE_MODEL_PATH))
        self.FACE_MODEL_PATH = str(self.weight_path(self.FACE_MODEL_PATH))
        self.EMOTION_MODEL_PATH = str(self.weight_path(self.EMOTION_MODEL_PATH))
        self.DEMOGRAPHICS_MODEL_PATH = str(self.weight_path(self.DEMOGRAPHICS_MODEL_PATH))
        self.REID_WEIGHTS_PATH = str(self.weight_path(self.REID_WEIGHTS_PATH))
        self.PRODUCT_MODEL_PATH = str(self.weight_path(self.PRODUCT_MODEL_PATH))
        return self

    def person_model_for(self, device: str) -> tuple:
        """``(weights_path, imgsz)`` of the person detector for a resolved device (``cpu``/``cuda``/``mps``)."""
        gpu = not str(device).lower().startswith("cpu")
        name = self.PERSON_MODEL or (self.PERSON_MODEL_GPU if gpu else self.PERSON_MODEL_CPU)
        return str(self.weight_path(name)), int(self.PERSON_IMGSZ_GPU if gpu else self.PERSON_IMGSZ_CPU)

    @property
    def reid_model_version(self) -> str:
        """Tag stored with every embedding; vectors from different weights are not comparable."""
        return Path(self.REID_WEIGHTS_PATH).stem[:50]

    def weight_path(self, name: str) -> Path:
        """Resolve a weight file name/path: absolute stays, relative goes under MODEL_WEIGHTS_DIR."""
        path = Path(name).expanduser()
        if path.is_absolute():
            return path
        return Path(self.MODEL_WEIGHTS_DIR) / path

    def configured_weight_paths(self) -> Dict[str, Path]:
        """Every weight file the running app is configured to load."""
        paths = {
            "FIRE_MODEL_PATH": self.weight_path(self.FIRE_MODEL_PATH),
            "REID_WEIGHTS_PATH": self.weight_path(self.REID_WEIGHTS_PATH),
        }
        if self.PERSON_MODEL:
            paths["PERSON_MODEL"] = self.weight_path(self.PERSON_MODEL)
        else:  # only the model for this machine's device is required
            dev = self.DEVICE.strip().lower()
            if dev == "auto":
                try:
                    import torch

                    dev = "cuda" if torch.cuda.is_available() else "cpu"
                except Exception:
                    dev = "cpu"
            key = "PERSON_MODEL_CPU" if dev.startswith("cpu") else "PERSON_MODEL_GPU"
            paths[key] = self.weight_path(getattr(self, key))
        return paths

    def missing_weight_paths(self) -> Dict[str, Path]:
        return {key: path for key, path in self.configured_weight_paths().items() if not path.is_file()}

    def check_weights_or_raise(self) -> None:
        """Fail loudly (one message, every missing path) if a configured weight is absent."""
        missing = self.missing_weight_paths()
        if missing:
            listing = "; ".join(f"{key} -> {path}" for key, path in missing.items())
            raise RuntimeError(
                f"Missing configured model weight file(s) in MODEL_WEIGHTS_DIR={self.MODEL_WEIGHTS_DIR}: "
                f"{listing}. Run scripts/download_default_weights.py or fix the setting."
            )


settings = Settings()
