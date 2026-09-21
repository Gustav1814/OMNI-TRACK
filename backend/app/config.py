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
    # Long enough that a working session is not interrupted mid-task; the
    # refresh token (7 days) is what actually keeps you signed in, and the
    # frontend now redeems it on a 401 instead of bouncing to /login.
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 120
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
    REID_SIMILARITY_THRESHOLD: float = 0.78  # Stricter matching keeps similar-looking shoppers separate
    REID_MATCH_MARGIN: float = 0.08           # Best match must clearly beat the runner-up
    REID_MIN_CROP_HEIGHT: int = 96            # Tiny crops produce unreliable appearance embeddings
    REID_EMBEDDINGS_PER_ID: int = 3           # A few strong views without over-broadening an identity
    DETECTION_CONFIDENCE: float = 0.5        # Min confidence to count a detection
    # Which classes the detector AND tracker keep. "all" (or "") = every class the
    # loaded model knows about; otherwise a comma-separated list of class ids,
    # e.g. "0,2,5,7" = person, car, bus, truck. Use "0" for person-only.
    # Person-driven analytics (Re-ID, crowd, shelf, checkout) always filter back
    # down to people regardless of this setting.
    DETECTION_CLASSES: str = "all"
    NMS_THRESHOLD: float = 0.45              # Non-max suppression (reduces duplicate boxes)
    DEVICE: str = "auto"                     # "auto", "cpu", "cuda", "mps" (for Apple M-series)

    # Old aliases for backward compatibility
    YOLO_MODEL_PATH: str = "yolo11n.pt"
    YOLO_CONFIDENCE: float = 0.5
    YOLO_NMS_THRESHOLD: float = 0.45
    FIRE_MODEL_PATH: str = "fire-smoke.pt"
    # The fire detector runs only for jobs that select FIRE_MODEL_PATH as their
    # primary model or as an extra model. Measured on two negative controls —
    # 60 sampled frames each from a
    # face close-up and a supermarket checkout, neither containing any fire or
    # smoke, decoded at the same DECODE_IMGSZ the pipeline uses:
    #
    #   threshold   face clip            checkout clip
    #   0.40        15 alerts (25% of frames)   2 alerts   <- the old default
    #   0.50         5 alerts ( 8% of frames)   0 alerts
    #   0.60         0 alerts                   0 alerts
    #
    # The highest-scoring false alarm anywhere was 0.552 — a human face read as
    # fire. At 0.40 a quarter of the frames of one person's face raised an
    # alert, which is not a safety feature, it is alert fatigue.
    #
    # CAVEAT: this is a false-positive measurement only. No footage containing
    # real fire was available, so the sweep has no positive control and cannot
    # show what 0.60 costs in sensitivity. Before relying on this in anything
    # that matters, run the same sweep over footage that does contain fire.
    FIRE_CONFIDENCE: float = 0.6
    ENABLE_FIRE_DETECTION: bool = True
    REID_MODEL_NAME: str = "osnet_x0_25"
    REID_BACKEND: str = "torchreid"          # torchreid | fastreid
    REID_WEIGHTS: str = ""                   # Optional FastReID checkpoint path
    SAM2_WEIGHTS: str = "sam2_b.pt"
    ENABLE_SAM2: bool = False                # Keep disabled by default on laptop

    # --- Tracking ---
    TRACKER_DEFAULT: str = "bytetrack.yaml"  # Stable and efficient for fixed retail CCTV
    TRACKER_REID: bool = False

    # --- Pipeline ---
    PROCESSING_FPS: int = 15                 # Frames/sec to process per camera
    MAX_CAMERAS: int = 16                    # Maximum simultaneous cameras
    FRAME_BUFFER_SIZE: int = 2               # Keep low = lower latency
    DEFAULT_SKIP_FRAMES: int = 1             # Process every Nth frame
    DECODE_IMGSZ: int = 0                    # Optional decode-time resize (0 = disabled)

    # --- Pluggable backends ---
    EVENT_BUS_BACKEND: str = "redis"         # redis | kafka
    # pgvector is the proposal's stated store and the only backend that PERSISTS:
    # FaissStore keeps vectors in process memory only, so embeddings vanish on
    # restart and the embeddings table stays empty.
    VECTOR_STORE_BACKEND: str = "pgvector"   # pgvector | faiss | qdrant
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

    # --- Emotion recognition ---
    # DeepFace costs 100-500ms per frame, so running it on every tick of every
    # camera would dominate the pipeline budget. Analyse every Nth tick instead
    # and reuse the last summary between runs (faces do not change that fast).
    EMOTION_EVERY_N_TICKS: int = 15
    # Face detector DeepFace uses before classifying. ssd is the fastest
    # (~0.04s/frame vs mtcnn ~0.35s) but never populates region.left_eye, and
    # analyze_frame drops any face without eye landmarks — so ssd yields zero
    # samples on every frame and sentiment silently stays 0. mtcnn returns
    # landmarks reliably and runs only every EMOTION_EVERY_N_TICKS ticks.
    EMOTION_DETECTOR_BACKEND: str = "mtcnn"
    # DeepFace runs with enforce_detection=False so a faceless frame does not raise.
    # The cost is that it then "analyses" the whole frame and returns a confident
    # emotion for it — a paintbrush measured as angry(0.92). Every such result
    # carries face_confidence == 0.0, so anything below this threshold is dropped.
    EMOTION_MIN_FACE_CONFIDENCE: float = 0.5
    ENABLE_EMOTION: bool = True

    # --- Demographics (age / gender) ---
    # Age and gender come out of the SAME DeepFace call as emotion, so they cost
    # two extra classifier heads rather than a second pass over the frame. They
    # therefore inherit EMOTION_EVERY_N_TICKS and the emotion face-quality gates.
    ENABLE_DEMOGRAPHICS: bool = True
    # Smallest face that may yield an age or a gender, in pixels on the short
    # side. Set from measurement on this project's own footage, not from taste:
    #
    #   36-43px faces  ages within 2.7 years (mean) of the SAME frames read at
    #                  full size, and gender correct every time. That is better
    #                  agreement than the model has with itself frame to frame,
    #                  where the spread on a 100px face is around 7 years.
    #   18-44px faces  (checkout clip, before the decode downscale) every read
    #                  came back "fear" or "sad" with a confident-looking score
    #                  — the signature of a face upsampled ~10x into a 224x224
    #                  model input.
    #
    # 35 is the bottom of the measured range. Below it there is no evidence,
    # so nothing is recorded: lowering this would not produce more data, it
    # would produce more noise. Note that faces are measured AFTER the
    # DECODE_IMGSZ downscale — see docs/DEMOGRAPHICS.md.
    DEMOGRAPHICS_MIN_FACE_PX: int = 35
    # A visitor's row is written once their track has gone this long without a
    # new face sample. One shopper then becomes ONE row carrying the median of
    # their samples, instead of one row per frame with ages that disagree.
    DEMOGRAPHICS_VISIT_TIMEOUT_S: float = 45.0
    # A face box must lie at least this far inside a person box to be attributed
    # to that person. Below it the face is counted but left unattributed.
    DEMOGRAPHICS_FACE_CONTAINMENT: float = 0.6

    # --- Jobs ---
    # Registered jobs, not running ones — a job can now sit idle between runs.
    # Each RUNNING job costs a decoder, a detector and a tracker, so the real
    # ceiling is CPU; registering more than you run is nearly free.
    MAX_JOBS: int = 12
    ROI_DWELL_ALERT_SECONDS: float = 30.0
    QUEUE_ALERT_LENGTH: int = 0                # People in a lane before alerting (0 = off)
    QUEUE_ALERT_SECONDS: float = 30.0          # ...and for how long, to ride out shuffling    # Dwell in one zone before an alert fires (0 = off)
    JOB_FLUSH_INTERVAL_S: float = 5.0        # How often live KPI state is written to Postgres

    # --- Job artifacts on disk (VisRax's shared/ais1 equivalent) ---
    ARTIFACTS_DIR: str = "shared/ais1"
    ARTIFACT_SNAPSHOTS: bool = True          # Save cropped detections
    ARTIFACT_CLIPS: bool = True              # Save annotated video segments
    ARTIFACT_SEGMENT_FRAMES: int = 150       # Frames per clip before rolling over
    ARTIFACT_SNAPSHOT_DEDUP_S: float = 1.0   # Min seconds between snapshots of one track
    ARTIFACT_QUOTA_MB: int = 2048            # Oldest files deleted past this budget

    # --- CCTV Footage storage ---
    FOOTAGE_DIR: str = "storage/footage"      # Where uploaded/recorded clips are stored

    class Config:
        # Resolve against backend/ so MAX_CAMERAS and friends apply even when uvicorn
        # is started from the repo root (cwd-relative ".env" would miss backend/.env).
        env_file = str(_backend_dir() / ".env")
        env_file_encoding = "utf-8"
        case_sensitive = True

    def detection_class_ids(self) -> List[int]:
        """
        DETECTION_CLASSES -> the `classes` filter Ultralytics expects.

        Returns [] for "all"/"" meaning *no* filter (keep every class the model
        detects). Unparseable entries are ignored rather than crashing startup.
        """
        raw = (self.DETECTION_CLASSES or "").strip().lower()
        if raw in ("", "all", "*", "any"):
            return []
        ids: List[int] = []
        for part in raw.replace(";", ",").split(","):
            part = part.strip()
            if not part:
                continue
            try:
                ids.append(int(part))
            except ValueError:
                continue
        return ids

    @model_validator(mode="after")
    def apply_runtime_profile_defaults(self) -> "Settings":
        profile = (self.RUNTIME_PROFILE or "laptop").strip().lower()
        object.__setattr__(self, "RUNTIME_PROFILE", profile)

        # Apply defaults only if user didn't explicitly override env values.
        # We intentionally keep model choice open so users can pick any weight.
        if profile == "laptop":
            # 8 was too low: counting KPIs need the pipeline to tick at least as
            # fast as the source. Below the video's own frame rate the tracker
            # sees jumps, identities fragment, and crossings go uncounted.
            if self.PROCESSING_FPS == 15:
                object.__setattr__(self, "PROCESSING_FPS", 15)
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
            object.__setattr__(self, "TRACKER_DEFAULT", "bytetrack.yaml")
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
            # Try the name as given, then with - and _ swapped. A missing weight
            # only produces a warning and a permanently silent detector, so a
            # punctuation mismatch is worth being forgiving about.
            name_variants = [name]
            for alt in (name.replace("_", "-"), name.replace("-", "_")):
                if alt not in name_variants:
                    name_variants.append(alt)

            candidates = [backend / ref, Path(ref)]
            for variant in name_variants:
                candidates.append(weights_dir / variant)
                candidates.append(backend / variant)

            for candidate in candidates:
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


# ─────────────────────────────────────────────────────────────────────
# Age buckets — defined ONCE
#
# The bucketer, the API and the frontend all used to carry their own list
# and all three disagreed, so a row written as "26-35" could land in a
# chart that had no such bar. This is now the single source; the API ships
# `age_groups` to the page so the frontend never hard-codes them either.
#
# (inclusive upper bound, label) — the last bucket is open-ended.
# ─────────────────────────────────────────────────────────────────────
AGE_BUCKETS: tuple = (
    (17, "<18"),
    (25, "18-25"),
    (35, "26-35"),
    (45, "36-45"),
    (55, "46-55"),
    (10_000, "56+"),
)

AGE_GROUPS: list = [label for _, label in AGE_BUCKETS]


def age_group(age) -> Optional[str]:
    """Bucket an estimated age. Returns None for a missing or absurd value."""
    try:
        value = float(age)
    except (TypeError, ValueError):
        return None
    if value <= 0 or value > 120:
        return None
    for upper, label in AGE_BUCKETS:
        if value <= upper:
            return label
    return AGE_BUCKETS[-1][1]


def normalize_gender(value) -> str:
    """
    DeepFace emits 'Man' / 'Woman'; every consumer here speaks
    'male' / 'female'. Anything else becomes 'unknown' rather than being
    written through as a label nothing can render.
    """
    text = str(value or "").strip().lower()
    if text in ("man", "male", "m"):
        return "male"
    if text in ("woman", "female", "f"):
        return "female"
    return "unknown"


def resolved_footage_dir() -> Path:
    """Absolute footage directory (backend-relative when FOOTAGE_DIR is relative)."""
    p = Path(settings.FOOTAGE_DIR)
    return p.resolve() if p.is_absolute() else (_backend_dir() / p).resolve()


def resolved_artifacts_dir() -> Path:
    """Absolute artifacts directory (backend-relative when ARTIFACTS_DIR is relative)."""
    p = Path(settings.ARTIFACTS_DIR)
    return p.resolve() if p.is_absolute() else (_backend_dir() / p).resolve()


def resolved_logs_dir() -> Path:
    """Detection JSON logs: backend/storage/logs."""
    return (_backend_dir() / "storage" / "logs").resolve()
