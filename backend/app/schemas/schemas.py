"""
OmniTrack AI — Pydantic Schemas
Request/response models for all API endpoints
"""

from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum
from app.models.user import UserRole


# ---- Auth ----

class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: str = Field(..., max_length=100)
    password: str = Field(..., min_length=6)
    full_name: Optional[str] = None


class UserLogin(BaseModel):
    username: str
    password: str


class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    full_name: Optional[str]
    role: UserRole
    is_active: bool
    created_at: Optional[datetime]

    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class TokenRefresh(BaseModel):
    refresh_token: str


# ---- Camera ----

class CameraCreate(BaseModel):
    name: str = Field(..., max_length=100)
    stream_url: str = Field(..., max_length=500)
    location: Optional[str] = None
    zone: Optional[str] = None
    resolution_w: int = 1920
    resolution_h: int = 1080
    fps: float = 30.0
    camera_type: str = "general"
    roi_config: Optional[Dict[str, Any]] = None


class CameraUpdate(BaseModel):
    name: Optional[str] = None
    stream_url: Optional[str] = None
    location: Optional[str] = None
    zone: Optional[str] = None
    is_active: Optional[bool] = None
    camera_type: Optional[str] = None
    roi_config: Optional[Dict[str, Any]] = None


class CameraResponse(BaseModel):
    id: int
    name: str
    stream_url: str
    location: Optional[str]
    zone: Optional[str]
    resolution_w: int
    resolution_h: int
    fps: float
    is_active: bool
    camera_type: str
    roi_config: Optional[Dict[str, Any]]
    created_at: Optional[datetime]

    class Config:
        from_attributes = True


# ---- Detection ----

class DetectionResult(BaseModel):
    camera_id: int
    track_id: Optional[int]
    bbox: List[float] = Field(..., description="[x, y, w, h]")
    confidence: float
    class_name: str = "person"
    zone: Optional[str] = None
    timestamp: Optional[datetime] = None
    model_path: Optional[str] = None
    ensemble_models: Optional[List[str]] = None


class DetectionFrame(BaseModel):
    camera_id: int
    frame_number: int
    detections: List[DetectionResult]
    fps: float
    processing_time_ms: float


# ---- Re-ID ----

class ReIDQuery(BaseModel):
    embedding: Optional[List[float]] = None
    image_base64: Optional[str] = None
    top_k: int = 10
    threshold: float = 0.6


class ReIDMatch(BaseModel):
    global_id: str
    camera_id: int
    confidence: float
    timestamp: datetime
    bbox: Optional[List[float]] = None


class ReIDJourney(BaseModel):
    global_id: str
    journey: List[Dict[str, Any]]
    total_duration: float
    zones_visited: int
    first_seen: datetime
    last_seen: datetime


# ---- Video Synopsis ----

class SynopsisRequest(BaseModel):
    camera_id: int
    start_time: datetime
    end_time: datetime
    compression_ratio: float = 10.0


class SynopsisResponse(BaseModel):
    id: int
    camera_id: int
    original_duration: float
    synopsis_duration: float
    compression_ratio: float
    output_path: str
    status: str


class LicensePlatePrediction(BaseModel):
    text: str
    confidence: float
    bbox: List[float] = Field(..., description="[x, y, w, h]")
    region: Optional[str] = None
    ocr_confidence: Optional[float] = None


class LicensePlateResponse(BaseModel):
    detector_model: str
    ocr_model: str
    predictions: List[LicensePlatePrediction]
    annotated_image_base64: str


class LicensePlateModelsResponse(BaseModel):
    detector_models: List[str]
    ocr_models: List[str]


# ---- Shelf Analytics ----

class ShelfZone(BaseModel):
    zone_id: str
    zone_name: str
    bbox: List[float]  # [x1, y1, x2, y2]
    camera_id: int


class ShelfEngagement(BaseModel):
    zone_id: str
    zone_name: str
    avg_dwell_time: float
    visit_count: int
    engagement_score: float
    rank: int


# ---- Fire / Smoke ----

class FireAlert(BaseModel):
    camera_id: int
    alert_type: str  # "fire" or "smoke"
    confidence: float
    bbox: List[float]
    timestamp: datetime
    zone: Optional[str] = None


# ---- Crowd Density ----

class CrowdStatus(BaseModel):
    zone: str
    person_count: int
    density: float  # persons per sq meter
    classification: str  # "low", "medium", "high", "critical"
    threshold: float
    camera_id: int


class CrowdZoneConfig(BaseModel):
    zone_name: str
    camera_id: int
    area_sqm: float = 50.0
    threshold_medium: int = 5
    threshold_high: int = 10
    threshold_critical: int = 20


# ---- Checkout ----

class CheckoutMetrics(BaseModel):
    lane_id: str
    queue_length: int
    avg_service_time: float  # seconds
    throughput: float  # customers/hour
    current_wait_estimate: float  # seconds
    camera_id: int


# ---- Emotion ----

class EmotionResult(BaseModel):
    dominant_emotion: str
    confidence: float
    all_emotions: Dict[str, float]  # {"happy": 0.8, "sad": 0.1, ...}
    zone: Optional[str] = None
    camera_id: int


class EmotionZoneAggregation(BaseModel):
    zone: str
    dominant_emotion: str
    emotion_distribution: Dict[str, float]
    sample_count: int
    sentiment_score: float  # -1 to 1


# ---- Audit ----

class AuditEntry(BaseModel):
    id: int
    event_type: str
    user_id: Optional[int]
    description: Optional[str]
    current_hash: str
    previous_hash: Optional[str]
    timestamp: datetime
    is_valid: Optional[bool] = None

    class Config:
        from_attributes = True


class AuditChainStatus(BaseModel):
    valid: bool
    broken_at: Optional[int]
    total: int


# ---- Store Vibe (NEW) ----

class StoreVibe(BaseModel):
    overall_score: float = Field(..., ge=0, le=100)
    sentiment_score: float
    energy_score: float
    engagement_score: float
    foot_traffic_score: float
    breakdown: Optional[Dict[str, Any]] = None
    timestamp: datetime
    vibe_label: str  # "Buzzing", "Calm", "Energetic", "Quiet"


# ---- Foot Traffic ----

class FootTrafficData(BaseModel):
    zone: str
    person_count: int
    avg_dwell_time: float
    heatmap_data: Optional[Dict[str, Any]] = None
    interval_start: datetime
    interval_end: datetime


# ---- Demographics ----

class DemographicData(BaseModel):
    zone: Optional[str]
    age_distribution: Dict[str, int]  # {"18-25": 10, "26-35": 15, ...}
    gender_distribution: Dict[str, int]  # {"male": 25, "female": 30}
    total_count: int


# ---- Peak Hours ----

class PeakHourData(BaseModel):
    hour: int
    visitor_count: int
    avg_dwell_time: float
    busiest_zone: Optional[str]


class PeakHoursSummary(BaseModel):
    date: str
    peak_hour: int
    peak_count: int
    total_visitors: int
    hourly_data: List[PeakHourData]


# ---- Customer Journey ----

class CustomerJourneyResponse(BaseModel):
    global_id: str
    entry_time: datetime
    exit_time: Optional[datetime]
    total_duration: Optional[float]
    zones_visited: int
    journey_data: List[Dict[str, Any]]


# ---- Humanless Store / Virtual Cart ----

class ProductCreate(BaseModel):
    sku: str = Field(..., min_length=1, max_length=80)
    name: str = Field(..., min_length=1, max_length=160)
    category: Optional[str] = None
    price: float = Field(0.0, ge=0)
    unit: str = "item"
    image_url: Optional[str] = None
    product_metadata: Optional[Dict[str, Any]] = None


class ProductUpdate(BaseModel):
    sku: Optional[str] = None
    name: Optional[str] = None
    category: Optional[str] = None
    price: Optional[float] = Field(None, ge=0)
    unit: Optional[str] = None
    image_url: Optional[str] = None
    is_active: Optional[bool] = None
    product_metadata: Optional[Dict[str, Any]] = None


class ProductResponse(BaseModel):
    id: int
    sku: str
    name: str
    category: Optional[str]
    price: float
    unit: str
    image_url: Optional[str]
    is_active: bool
    product_metadata: Optional[Dict[str, Any]]
    created_at: Optional[datetime]

    class Config:
        from_attributes = True


class ShelfZoneCreate(BaseModel):
    zone_id: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=160)
    camera_id: Optional[int] = None
    product_id: Optional[int] = None
    bbox: Optional[List[float]] = None
    current_stock: int = 0
    low_stock_threshold: int = 3


class ShelfZoneUpdate(BaseModel):
    zone_id: Optional[str] = None
    name: Optional[str] = None
    camera_id: Optional[int] = None
    product_id: Optional[int] = None
    bbox: Optional[List[float]] = None
    current_stock: Optional[int] = None
    low_stock_threshold: Optional[int] = None
    is_active: Optional[bool] = None


class ShelfZoneResponse(BaseModel):
    id: int
    zone_id: str
    name: str
    camera_id: Optional[int]
    product_id: Optional[int]
    bbox: Optional[List[float]]
    current_stock: int
    low_stock_threshold: int
    is_active: bool
    created_at: Optional[datetime]

    class Config:
        from_attributes = True


class CartEventCreate(BaseModel):
    session_id: Optional[int] = None
    global_id: Optional[str] = None
    product_id: Optional[int] = None
    shelf_zone_id: Optional[int] = None
    camera_id: Optional[int] = None
    event_type: str = Field("pickup", pattern="^(pickup|putback|adjustment|checkout|uncertain)$")
    quantity_delta: int = 1
    confidence: float = Field(1.0, ge=0, le=1)
    rule_source: str = "manual"
    evidence: Optional[Dict[str, Any]] = None


class CartEventResponse(BaseModel):
    id: int
    cart_id: int
    session_id: int
    product_id: Optional[int]
    shelf_zone_id: Optional[int]
    camera_id: Optional[int]
    global_id: Optional[str]
    event_type: str
    quantity_delta: int
    confidence: float
    rule_source: str
    evidence: Optional[Dict[str, Any]]
    timestamp: datetime

    class Config:
        from_attributes = True


class VirtualCartItemResponse(BaseModel):
    id: int
    product_id: int
    sku: str
    name: str
    quantity: int
    unit_price: float
    line_total: float
    confidence: float


class VirtualCartResponse(BaseModel):
    id: int
    session_id: int
    status: str
    subtotal: float
    item_count: int
    confidence: float
    items: List[VirtualCartItemResponse] = []
    recent_events: List[CartEventResponse] = []


class StoreSessionResponse(BaseModel):
    id: int
    global_id: str
    status: str
    entry_time: datetime
    exit_time: Optional[datetime]
    last_seen: datetime
    entry_camera_id: Optional[int]
    last_camera_id: Optional[int]
    entry_zone: Optional[str]
    last_zone: Optional[str]
    checkout_confidence: float
    payment_status: str
    cart: Optional[VirtualCartResponse] = None


class HumanlessOverview(BaseModel):
    active_sessions: int
    open_carts: int
    subtotal_open: float
    low_stock_zones: int
    open_alerts: int
    sessions: List[StoreSessionResponse]


class CheckoutSimulationResponse(BaseModel):
    session_id: int
    cart_id: int
    status: str
    subtotal: float
    item_count: int
    confidence: float
    receipt: Dict[str, Any]


class CounterArrivalRequest(BaseModel):
    counter_id: Optional[str] = "checkout"
    confidence: float = Field(1.0, ge=0, le=1)


class CashierQueueItem(StoreSessionResponse):
    cashier_ready: bool = True
    counter_id: Optional[str] = None


class LossPreventionAlertResponse(BaseModel):
    id: int
    session_id: Optional[int]
    cart_id: Optional[int]
    alert_type: str
    severity: str
    status: str
    description: Optional[str]
    confidence: float
    evidence: Optional[Dict[str, Any]]
    timestamp: datetime

    class Config:
        from_attributes = True


# ---- Store Setup ----

class StoreTemplate(BaseModel):
    id: str
    label: str
    modules: List[str]
    default_zones: List[str]
    description: Optional[str] = None


class StoreProfileUpdate(BaseModel):
    store_name: Optional[str] = Field(None, min_length=1, max_length=160)
    deployment_type: Optional[str] = Field(None, max_length=80)
    enabled_modules: Optional[List[str]] = None
    zone_templates: Optional[List[str]] = None
    setup_completed: Optional[bool] = None


class StoreProfileResponse(BaseModel):
    id: int
    store_name: str
    deployment_type: str
    enabled_modules: List[str]
    zone_templates: List[str]
    setup_completed_at: Optional[datetime]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


# ---- Dashboard Overview ----

class DashboardOverview(BaseModel):
    total_cameras: int
    active_cameras: int
    total_detections_today: int
    current_occupancy: int
    fire_alerts_today: int
    avg_checkout_wait: float
    store_vibe: StoreVibe
    peak_hour_today: Optional[int]
    top_zone: Optional[str]
