"""
OmniTrack AI — WebSocket Broadcast Service
Real-time push notifications to all connected dashboard clients.

HOW IT WORKS:
  When the pipeline detects something important (fire alert, overcrowding,
  new person detected), it pushes the event through WebSocket to ALL
  connected dashboard browsers — instant updates without polling.

  Dashboard connects to: ws://your-server/ws/live
  Events arrive as JSON: { "type": "fire_alert", "data": {...} }
"""

import asyncio
import json
from typing import Dict, List, Any, Set, Optional
from fastapi import WebSocket, WebSocketDisconnect
from loguru import logger
from datetime import datetime, timezone


class BroadcastService:
    """
    Manages WebSocket connections and broadcasts events to dashboards.
    
    Supports multiple channels:
      - "alerts"    → Fire/smoke, overcrowding
      - "detections" → New person detections
      - "analytics" → Real-time analytics updates
      - "vibe"      → Store Vibe Score changes
      - "all"       → Everything (default for dashboard)
    """

    def __init__(self):
        # channel → set of websockets
        self._subscribers: Dict[str, Set[WebSocket]] = {
            "alerts": set(),
            "detections": set(),
            "analytics": set(),
            "vibe": set(),
            "all": set(),
        }
        self._total_connections = 0
        self._total_messages_sent = 0

    async def subscribe(self, ws: WebSocket, channel: str = "all"):
        """Register a WebSocket client to a channel."""
        await ws.accept()
        if channel not in self._subscribers:
            self._subscribers[channel] = set()
        self._subscribers[channel].add(ws)
        self._total_connections += 1
        logger.info(f"WebSocket subscribed to '{channel}' (total: {len(self._subscribers[channel])})")

    def unsubscribe(self, ws: WebSocket, channel: str = "all"):
        """Remove a client from a channel."""
        if channel in self._subscribers:
            self._subscribers[channel].discard(ws)

    def unsubscribe_all(self, ws: WebSocket):
        """Remove a client from ALL channels."""
        for ch in self._subscribers.values():
            ch.discard(ws)

    async def broadcast(self, channel: str, event_type: str, data: Any):
        """
        Send an event to all subscribers of a channel + the "all" channel.
        
        Args:
            channel: "alerts", "detections", "analytics", "vibe"
            event_type: Specific event name (e.g., "fire_alert", "new_person")
            data: Event payload (must be JSON-serializable)
        """
        message = {
            "type": event_type,
            "channel": channel,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        payload = json.dumps(message, default=str)

        # Send to specific channel subscribers + "all" subscribers
        targets = self._subscribers.get(channel, set()) | self._subscribers.get("all", set())
        disconnected = set()

        for ws in targets:
            try:
                await ws.send_text(payload)
                self._total_messages_sent += 1
            except Exception:
                disconnected.add(ws)

        # Clean up dead connections
        for ws in disconnected:
            self.unsubscribe_all(ws)

    # ─────────────────────────────────────────────────────────
    # CONVENIENCE METHODS (called from pipeline callbacks)
    # ─────────────────────────────────────────────────────────

    async def push_fire_alert(self, camera_id: int, alert_type: str, confidence: float, zone: str):
        """Broadcast a fire/smoke alert."""
        await self.broadcast("alerts", "fire_alert", {
            "camera_id": camera_id,
            "alert_type": alert_type,
            "confidence": round(confidence, 3),
            "zone": zone,
            "severity": "critical",
        })

    async def push_crowd_alert(self, zone: str, count: int, level: str):
        """Broadcast a crowd density warning."""
        await self.broadcast("alerts", "crowd_alert", {
            "zone": zone,
            "person_count": count,
            "density_level": level,
        })

    async def push_detection_update(self, camera_id: int, count: int, tracks: list):
        """Broadcast detection update for a camera."""
        await self.broadcast("detections", "detection_update", {
            "camera_id": camera_id,
            "person_count": count,
            "active_tracks": len(tracks),
        })

    async def push_vibe_update(self, score: float, label: str, breakdown: dict):
        """Broadcast Store Vibe Score update."""
        await self.broadcast("vibe", "vibe_update", {
            "overall_score": round(score, 1),
            "label": label,
            "breakdown": breakdown,
        })

    async def push_reid_match(
        self,
        global_id: str,
        camera_id: int,
        previous_camera: int,
        similarity: Optional[float] = None,
        snapshot_previous: Optional[str] = None,
        snapshot_current: Optional[str] = None,
    ):
        """Broadcast cross-camera Re-ID match (optional base64 JPEG crops, no data: URL prefix)."""
        payload: Dict[str, Any] = {
            "global_id": global_id,
            "current_camera": camera_id,
            "previous_camera": previous_camera,
        }
        if similarity is not None:
            payload["similarity"] = round(float(similarity), 4)
        if snapshot_previous:
            payload["snapshot_previous"] = snapshot_previous
        if snapshot_current:
            payload["snapshot_current"] = snapshot_current
        await self.broadcast("detections", "reid_match", payload)

    # ─────────────────────────────────────────────────────────
    # STATS
    # ─────────────────────────────────────────────────────────

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "total_connections": self._total_connections,
            "active_connections": sum(len(s) for s in self._subscribers.values()),
            "total_messages_sent": self._total_messages_sent,
            "channels": {ch: len(subs) for ch, subs in self._subscribers.items()},
        }
