"""
OmniTrack AI - Humanless Store Router

Amazon Go-style foundation optimized for low-cost shops: regular cameras,
zone mapping, virtual carts, confidence review, and simulated checkout.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User, UserRole
from app.schemas.schemas import (
    CashierQueueItem,
    CartEventCreate,
    CartEventResponse,
    CheckoutSimulationResponse,
    CounterArrivalRequest,
    HumanlessOverview,
    LossPreventionAlertResponse,
    ProductCreate,
    ProductResponse,
    ProductUpdate,
    ShelfZoneCreate,
    ShelfZoneResponse,
    ShelfZoneUpdate,
    StoreSessionResponse,
)
from app.security.dependencies import get_current_user, require_role
from app.services.humanless_store import HumanlessStoreService


router = APIRouter(prefix="/api/humanless", tags=["Humanless Store"])


@router.get("/overview", response_model=HumanlessOverview)
async def overview(
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await HumanlessStoreService.overview(db, limit=limit)


@router.get("/products", response_model=List[ProductResponse])
async def list_products(
    active_only: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await HumanlessStoreService.list_products(db, active_only=active_only)


@router.post("/products", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product(
    data: ProductCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.ADMIN, UserRole.OPERATOR])),
):
    return await HumanlessStoreService.create_product(db, data.model_dump())


@router.put("/products/{product_id}", response_model=ProductResponse)
async def update_product(
    product_id: int,
    data: ProductUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.ADMIN, UserRole.OPERATOR])),
):
    product = await HumanlessStoreService.update_product(
        db, product_id, data.model_dump(exclude_unset=True)
    )
    if product is None:
        raise HTTPException(404, "Product not found")
    return product


@router.get("/shelf-zones", response_model=List[ShelfZoneResponse])
async def list_shelf_zones(
    active_only: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await HumanlessStoreService.list_shelf_zones(db, active_only=active_only)


@router.post("/shelf-zones", response_model=ShelfZoneResponse, status_code=status.HTTP_201_CREATED)
async def create_shelf_zone(
    data: ShelfZoneCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.ADMIN, UserRole.OPERATOR])),
):
    return await HumanlessStoreService.create_shelf_zone(db, data.model_dump())


@router.put("/shelf-zones/{zone_id}", response_model=ShelfZoneResponse)
async def update_shelf_zone(
    zone_id: int,
    data: ShelfZoneUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.ADMIN, UserRole.OPERATOR])),
):
    zone = await HumanlessStoreService.update_shelf_zone(
        db, zone_id, data.model_dump(exclude_unset=True)
    )
    if zone is None:
        raise HTTPException(404, "Shelf zone not found")
    return zone


@router.post("/cart-events", response_model=CartEventResponse, status_code=status.HTTP_201_CREATED)
async def create_cart_event(
    data: CartEventCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.ADMIN, UserRole.OPERATOR])),
):
    try:
        return await HumanlessStoreService.apply_cart_event(db, data.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/sessions/{session_id}/checkout", response_model=CheckoutSimulationResponse)
async def checkout_session(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.ADMIN, UserRole.OPERATOR])),
):
    try:
        return await HumanlessStoreService.checkout_simulation(db, session_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/cashier/queue", response_model=List[CashierQueueItem])
async def cashier_queue(
    zone: Optional[str] = None,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await HumanlessStoreService.cashier_queue(db, zone=zone, limit=limit)


@router.post("/sessions/{session_id}/counter-arrival", response_model=StoreSessionResponse)
async def counter_arrival(
    session_id: int,
    data: CounterArrivalRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.ADMIN, UserRole.OPERATOR])),
):
    try:
        return await HumanlessStoreService.mark_counter_arrival(
            db,
            session_id=session_id,
            counter_id=data.counter_id,
            confidence=data.confidence,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/sessions/close-stale")
async def close_stale_sessions(
    stale_after_seconds: int = 300,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.ADMIN, UserRole.OPERATOR])),
):
    count = await HumanlessStoreService.close_stale_sessions(
        db,
        stale_after_seconds=max(30, stale_after_seconds),
    )
    return {"closed": count}


@router.get("/alerts", response_model=List[LossPreventionAlertResponse])
async def list_alerts(
    status: Optional[str] = "open",
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await HumanlessStoreService.list_alerts(db, status=status, limit=limit)
