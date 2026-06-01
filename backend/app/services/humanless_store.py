"""
Humanless Store Service

Vision-first, low-cost cashierless workflows:
  - Re-ID global_id -> active store session
  - session -> virtual cart
  - shelf zone/product map -> manual or AI cart events
  - confidence/evidence -> reviewable loss-prevention alerts
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.humanless import (
    CartEvent,
    CartItem,
    LossPreventionAlert,
    Product,
    ShelfZone,
    StoreSession,
    VirtualCart,
)


OPEN_SESSION_STATUSES = ("active", "checkout_pending")
OPEN_CART_STATUSES = ("open", "review")
COUNTER_ZONE_KEYWORDS = ("checkout", "counter", "cashier", "billing", "pos")


class HumanlessStoreService:
    """CRUD and cart/session rules for the cashierless module."""

    @staticmethod
    async def create_product(db: AsyncSession, data: Dict[str, Any]) -> Product:
        product = Product(**data)
        db.add(product)
        await db.flush()
        await db.refresh(product)
        return product

    @staticmethod
    async def list_products(db: AsyncSession, active_only: bool = False) -> List[Product]:
        stmt = select(Product).order_by(Product.name.asc())
        if active_only:
            stmt = stmt.where(Product.is_active == True)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def update_product(db: AsyncSession, product_id: int, data: Dict[str, Any]) -> Optional[Product]:
        product = await db.get(Product, product_id)
        if product is None:
            return None
        for key, value in data.items():
            if value is not None and hasattr(product, key):
                setattr(product, key, value)
        await db.flush()
        await db.refresh(product)
        return product

    @staticmethod
    async def create_shelf_zone(db: AsyncSession, data: Dict[str, Any]) -> ShelfZone:
        zone = ShelfZone(**data)
        db.add(zone)
        await db.flush()
        await db.refresh(zone)
        return zone

    @staticmethod
    async def list_shelf_zones(db: AsyncSession, active_only: bool = False) -> List[ShelfZone]:
        stmt = select(ShelfZone).order_by(ShelfZone.camera_id.asc(), ShelfZone.name.asc())
        if active_only:
            stmt = stmt.where(ShelfZone.is_active == True)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def update_shelf_zone(db: AsyncSession, zone_id: int, data: Dict[str, Any]) -> Optional[ShelfZone]:
        zone = await db.get(ShelfZone, zone_id)
        if zone is None:
            return None
        for key, value in data.items():
            if value is not None and hasattr(zone, key):
                setattr(zone, key, value)
        await db.flush()
        await db.refresh(zone)
        return zone

    @staticmethod
    async def ensure_session_for_global_id(
        db: AsyncSession,
        global_id: str,
        camera_id: Optional[int] = None,
        zone: Optional[str] = None,
        seen_at: Optional[datetime] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[StoreSession, VirtualCart, bool]:
        """
        Ensure one active session/cart exists for a Re-ID global_id.
        Returns (session, cart, created_session).
        """
        now = seen_at or datetime.now(timezone.utc)
        result = await db.execute(
            select(StoreSession)
            .where(
                StoreSession.global_id == global_id,
                StoreSession.status.in_(OPEN_SESSION_STATUSES),
            )
            .order_by(desc(StoreSession.last_seen))
            .limit(1)
        )
        session = result.scalar_one_or_none()
        created = False

        if session is None:
            session = StoreSession(
                global_id=global_id,
                status="active",
                entry_time=now,
                last_seen=now,
                entry_camera_id=camera_id,
                last_camera_id=camera_id,
                entry_zone=zone,
                last_zone=zone,
                session_metadata=metadata,
            )
            db.add(session)
            await db.flush()
            created = True
        else:
            session.last_seen = now
            session.last_camera_id = camera_id or session.last_camera_id
            session.last_zone = zone or session.last_zone
            if metadata:
                merged = dict(session.session_metadata or {})
                merged.update(metadata)
                session.session_metadata = merged

        cart = await HumanlessStoreService._get_or_create_cart(db, session.id)
        await db.flush()
        return session, cart, created

    @staticmethod
    async def _get_or_create_cart(db: AsyncSession, session_id: int) -> VirtualCart:
        result = await db.execute(select(VirtualCart).where(VirtualCart.session_id == session_id))
        cart = result.scalar_one_or_none()
        if cart is not None:
            return cart
        cart = VirtualCart(session_id=session_id, status="open", subtotal=0.0, item_count=0, confidence=1.0)
        db.add(cart)
        await db.flush()
        return cart

    @staticmethod
    async def apply_cart_event(db: AsyncSession, data: Dict[str, Any]) -> CartEvent:
        """
        Append a cart event and update current cart state. Product can be
        supplied directly or inferred from a shelf zone.
        """
        session = None
        if data.get("session_id"):
            session = await db.get(StoreSession, int(data["session_id"]))
        elif data.get("global_id"):
            session, _cart, _created = await HumanlessStoreService.ensure_session_for_global_id(
                db,
                global_id=str(data["global_id"]),
                camera_id=data.get("camera_id"),
                zone=None,
                metadata={"created_by": "cart_event"},
            )
        if session is None:
            raise ValueError("session_id or global_id is required")

        cart = await HumanlessStoreService._get_or_create_cart(db, session.id)

        product_id = data.get("product_id")
        shelf_zone_id = data.get("shelf_zone_id")
        if not product_id and shelf_zone_id:
            zone = await db.get(ShelfZone, int(shelf_zone_id))
            product_id = zone.product_id if zone else None
        product = await db.get(Product, int(product_id)) if product_id else None

        event_type = str(data.get("event_type") or "pickup")
        quantity_delta = int(data.get("quantity_delta") or 0)
        if event_type == "pickup" and quantity_delta == 0:
            quantity_delta = 1
        elif event_type == "putback" and quantity_delta == 0:
            quantity_delta = -1
        elif event_type == "putback" and quantity_delta > 0:
            quantity_delta = -quantity_delta
        elif event_type == "checkout":
            quantity_delta = 0

        event = CartEvent(
            cart_id=cart.id,
            session_id=session.id,
            product_id=product.id if product else None,
            shelf_zone_id=shelf_zone_id,
            camera_id=data.get("camera_id"),
            global_id=session.global_id,
            event_type=event_type,
            quantity_delta=quantity_delta,
            confidence=float(data.get("confidence", 1.0)),
            rule_source=str(data.get("rule_source") or "manual"),
            evidence=data.get("evidence"),
        )
        db.add(event)
        await db.flush()

        if product and quantity_delta:
            await HumanlessStoreService._apply_item_delta(
                db,
                cart=cart,
                product=product,
                quantity_delta=quantity_delta,
                confidence=event.confidence,
                event_id=event.id,
            )

        if event.confidence < 0.65 or event_type == "uncertain":
            await HumanlessStoreService.create_alert(
                db,
                session_id=session.id,
                cart_id=cart.id,
                alert_type="low_confidence_cart_event",
                severity="medium",
                description=f"Review {event_type} for {product.name if product else 'unknown product'}",
                confidence=event.confidence,
                evidence=event.evidence,
            )

        await HumanlessStoreService.recalculate_cart(db, cart.id)
        await db.refresh(event)
        return event

    @staticmethod
    async def _apply_item_delta(
        db: AsyncSession,
        cart: VirtualCart,
        product: Product,
        quantity_delta: int,
        confidence: float,
        event_id: int,
    ) -> None:
        result = await db.execute(
            select(CartItem).where(
                CartItem.cart_id == cart.id,
                CartItem.product_id == product.id,
            )
        )
        item = result.scalar_one_or_none()
        if item is None:
            item = CartItem(
                cart_id=cart.id,
                product_id=product.id,
                quantity=0,
                unit_price=product.price,
                confidence=confidence,
            )
            db.add(item)
            await db.flush()

        item.quantity = max(0, int(item.quantity or 0) + quantity_delta)
        item.unit_price = product.price
        item.confidence = min(float(item.confidence or 1.0), confidence)
        item.last_event_id = event_id

    @staticmethod
    async def recalculate_cart(db: AsyncSession, cart_id: int) -> Optional[VirtualCart]:
        cart = await db.get(VirtualCart, cart_id)
        if cart is None:
            return None
        result = await db.execute(select(CartItem).where(CartItem.cart_id == cart_id))
        items = list(result.scalars().all())
        active_items = [item for item in items if int(item.quantity or 0) > 0]
        cart.item_count = sum(int(item.quantity or 0) for item in active_items)
        cart.subtotal = round(sum((item.quantity or 0) * (item.unit_price or 0.0) for item in active_items), 2)
        cart.confidence = min([float(item.confidence or 1.0) for item in active_items] or [1.0])
        cart.status = "review" if cart.confidence < 0.65 and cart.status == "open" else cart.status
        cart.updated_at = datetime.now(timezone.utc)
        await db.flush()
        return cart

    @staticmethod
    async def create_alert(
        db: AsyncSession,
        session_id: Optional[int],
        cart_id: Optional[int],
        alert_type: str,
        severity: str,
        description: str,
        confidence: float,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> LossPreventionAlert:
        alert = LossPreventionAlert(
            session_id=session_id,
            cart_id=cart_id,
            alert_type=alert_type,
            severity=severity,
            description=description,
            confidence=confidence,
            evidence=evidence,
        )
        db.add(alert)
        await db.flush()
        return alert

    @staticmethod
    async def close_stale_sessions(db: AsyncSession, stale_after_seconds: int = 300) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_after_seconds)
        result = await db.execute(
            select(StoreSession).where(
                StoreSession.status == "active",
                StoreSession.last_seen < cutoff,
            )
        )
        sessions = list(result.scalars().all())
        for session in sessions:
            session.status = "checkout_pending"
            session.exit_time = session.last_seen
            session.payment_status = "review_required"
        await db.flush()
        return len(sessions)

    @staticmethod
    async def checkout_simulation(db: AsyncSession, session_id: int) -> Dict[str, Any]:
        session = await db.get(StoreSession, session_id)
        if session is None:
            raise ValueError("Session not found")
        cart = await HumanlessStoreService._get_or_create_cart(db, session.id)
        await HumanlessStoreService.recalculate_cart(db, cart.id)
        await db.refresh(cart)

        result = await db.execute(select(CartItem).where(CartItem.cart_id == cart.id, CartItem.quantity > 0))
        items = list(result.scalars().all())
        receipt_items = []
        for item in items:
            product = await db.get(Product, item.product_id)
            receipt_items.append({
                "product_id": item.product_id,
                "sku": product.sku if product else "",
                "name": product.name if product else f"Product {item.product_id}",
                "quantity": int(item.quantity or 0),
                "unit_price": float(item.unit_price or 0.0),
                "line_total": round((item.quantity or 0) * (item.unit_price or 0.0), 2),
                "confidence": float(item.confidence or 1.0),
            })

        cart.status = "paid" if cart.confidence >= 0.65 else "review"
        session.status = "paid" if cart.status == "paid" else "checkout_pending"
        session.payment_status = "simulated_paid" if cart.status == "paid" else "review_required"
        session.checkout_confidence = cart.confidence
        session.exit_time = session.exit_time or datetime.now(timezone.utc)

        await HumanlessStoreService.apply_cart_event(db, {
            "session_id": session.id,
            "event_type": "checkout",
            "confidence": cart.confidence,
            "rule_source": "checkout_simulation",
            "evidence": {"subtotal": cart.subtotal, "item_count": cart.item_count},
        })
        await db.flush()

        return {
            "session_id": session.id,
            "cart_id": cart.id,
            "status": cart.status,
            "subtotal": float(cart.subtotal or 0.0),
            "item_count": int(cart.item_count or 0),
            "confidence": float(cart.confidence or 0.0),
            "receipt": {
                "receipt_id": f"SIM-{session.id}-{cart.id}",
                "payment_status": session.payment_status,
                "items": receipt_items,
                "subtotal": float(cart.subtotal or 0.0),
            },
        }

    @staticmethod
    async def mark_counter_arrival(
        db: AsyncSession,
        session_id: int,
        counter_id: Optional[str] = None,
        confidence: float = 1.0,
    ) -> Dict[str, Any]:
        """
        Mark a shopper as visible at the cashier/POS counter. This does not
        charge the cart; it makes the current virtual bill ready for cashier
        review.
        """
        session = await db.get(StoreSession, session_id)
        if session is None:
            raise ValueError("Session not found")
        cart = await HumanlessStoreService._get_or_create_cart(db, session.id)
        await HumanlessStoreService.recalculate_cart(db, cart.id)
        await db.refresh(cart)

        session.status = "checkout_pending"
        session.payment_status = "bill_ready"
        session.checkout_confidence = min(float(confidence), float(cart.confidence or 1.0))
        session.last_zone = counter_id or session.last_zone or "checkout"
        metadata = dict(session.session_metadata or {})
        metadata["counter_id"] = counter_id
        metadata["counter_arrival_at"] = datetime.now(timezone.utc).isoformat()
        session.session_metadata = metadata

        await HumanlessStoreService.apply_cart_event(db, {
            "session_id": session.id,
            "event_type": "checkout",
            "confidence": session.checkout_confidence,
            "rule_source": "counter_arrival",
            "evidence": {
                "counter_id": counter_id,
                "subtotal": cart.subtotal,
                "item_count": cart.item_count,
            },
        })
        await db.flush()
        return await HumanlessStoreService._session_payload(db, session, cart)

    @staticmethod
    async def cashier_queue(
        db: AsyncSession,
        zone: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        Sessions whose bill should pop up for the cashier. A shopper qualifies
        if they were explicitly marked bill_ready/checkout_pending or their
        latest zone looks like a configured checkout/counter area.
        """
        result = await db.execute(
            select(StoreSession)
            .where(StoreSession.status.in_(OPEN_SESSION_STATUSES))
            .order_by(desc(StoreSession.last_seen))
            .limit(max(1, limit))
        )
        sessions = list(result.scalars().all())
        queue: List[Dict[str, Any]] = []
        zone_filter = (zone or "").lower().strip()

        for session in sessions:
            last_zone = (session.last_zone or "").lower()
            metadata = session.session_metadata or {}
            counter_id = str(metadata.get("counter_id") or "").lower()
            is_counter_zone = any(k in last_zone for k in COUNTER_ZONE_KEYWORDS)
            is_counter_zone = is_counter_zone or any(k in counter_id for k in COUNTER_ZONE_KEYWORDS)
            is_ready = session.status == "checkout_pending" or session.payment_status == "bill_ready"
            if zone_filter:
                is_counter_zone = zone_filter in last_zone or zone_filter in counter_id
            if not (is_ready or is_counter_zone):
                continue

            cart = await HumanlessStoreService._get_or_create_cart(db, session.id)
            payload = await HumanlessStoreService._session_payload(db, session, cart)
            payload["cashier_ready"] = True
            payload["counter_id"] = metadata.get("counter_id") or session.last_zone
            queue.append(payload)

        return queue

    @staticmethod
    async def list_alerts(db: AsyncSession, status: Optional[str] = "open", limit: int = 50) -> List[LossPreventionAlert]:
        stmt = select(LossPreventionAlert).order_by(desc(LossPreventionAlert.timestamp)).limit(limit)
        if status:
            stmt = stmt.where(LossPreventionAlert.status == status)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def overview(db: AsyncSession, limit: int = 20) -> Dict[str, Any]:
        sessions_result = await db.execute(
            select(StoreSession)
            .where(StoreSession.status.in_(OPEN_SESSION_STATUSES))
            .order_by(desc(StoreSession.last_seen))
            .limit(limit)
        )
        sessions = list(sessions_result.scalars().all())

        rows = []
        subtotal_open = 0.0
        open_carts = 0
        for session in sessions:
            cart = await HumanlessStoreService._get_or_create_cart(db, session.id)
            await HumanlessStoreService.recalculate_cart(db, cart.id)
            await db.refresh(cart)
            if cart.status in OPEN_CART_STATUSES:
                open_carts += 1
                subtotal_open += float(cart.subtotal or 0.0)
            rows.append(await HumanlessStoreService._session_payload(db, session, cart))

        low_stock = await db.execute(
            select(func.count(ShelfZone.id)).where(
                ShelfZone.is_active == True,
                ShelfZone.current_stock <= ShelfZone.low_stock_threshold,
            )
        )
        open_alerts = await db.execute(
            select(func.count(LossPreventionAlert.id)).where(LossPreventionAlert.status == "open")
        )
        return {
            "active_sessions": len(sessions),
            "open_carts": open_carts,
            "subtotal_open": round(subtotal_open, 2),
            "low_stock_zones": int(low_stock.scalar() or 0),
            "open_alerts": int(open_alerts.scalar() or 0),
            "sessions": rows,
        }

    @staticmethod
    async def _session_payload(db: AsyncSession, session: StoreSession, cart: VirtualCart) -> Dict[str, Any]:
        return {
            "id": session.id,
            "global_id": session.global_id,
            "status": session.status,
            "entry_time": session.entry_time,
            "exit_time": session.exit_time,
            "last_seen": session.last_seen,
            "entry_camera_id": session.entry_camera_id,
            "last_camera_id": session.last_camera_id,
            "entry_zone": session.entry_zone,
            "last_zone": session.last_zone,
            "checkout_confidence": session.checkout_confidence,
            "payment_status": session.payment_status,
            "cart": await HumanlessStoreService.cart_payload(db, cart.id),
        }

    @staticmethod
    async def cart_payload(db: AsyncSession, cart_id: int) -> Dict[str, Any]:
        cart = await db.get(VirtualCart, cart_id)
        if cart is None:
            raise ValueError("Cart not found")
        await HumanlessStoreService.recalculate_cart(db, cart.id)
        await db.refresh(cart)

        item_result = await db.execute(
            select(CartItem).where(CartItem.cart_id == cart.id, CartItem.quantity > 0)
        )
        item_rows = []
        for item in item_result.scalars().all():
            product = await db.get(Product, item.product_id)
            item_rows.append({
                "id": item.id,
                "product_id": item.product_id,
                "sku": product.sku if product else "",
                "name": product.name if product else f"Product {item.product_id}",
                "quantity": int(item.quantity or 0),
                "unit_price": float(item.unit_price or 0.0),
                "line_total": round((item.quantity or 0) * (item.unit_price or 0.0), 2),
                "confidence": float(item.confidence or 1.0),
            })

        event_result = await db.execute(
            select(CartEvent)
            .where(CartEvent.cart_id == cart.id)
            .order_by(desc(CartEvent.timestamp))
            .limit(10)
        )
        events = list(event_result.scalars().all())

        return {
            "id": cart.id,
            "session_id": cart.session_id,
            "status": cart.status,
            "subtotal": float(cart.subtotal or 0.0),
            "item_count": int(cart.item_count or 0),
            "confidence": float(cart.confidence or 0.0),
            "items": item_rows,
            "recent_events": events,
        }

    @staticmethod
    async def process_pipeline_tick(
        db: AsyncSession,
        results: Dict[int, Any],
        camera_zones: Dict[int, str],
    ) -> int:
        """
        Cheap automation pass: every Re-ID match becomes/refreshes an active
        store session and open virtual cart. Product events are layered later.
        """
        touched = 0
        for cam_id, result in results.items():
            zone = camera_zones.get(cam_id, f"cam-{cam_id}")
            for match in (getattr(result, "reid_matches", None) or []):
                global_id = match.get("global_id")
                if not global_id:
                    continue
                seen_at = datetime.fromtimestamp(
                    float(getattr(result, "timestamp", 0) or 0),
                    tz=timezone.utc,
                )
                await HumanlessStoreService.ensure_session_for_global_id(
                    db,
                    global_id=global_id,
                    camera_id=int(cam_id),
                    zone=zone,
                    seen_at=seen_at,
                    metadata={
                        "last_bbox": match.get("bbox"),
                        "source": "pipeline_reid",
                    },
                )
                touched += 1
        return touched
