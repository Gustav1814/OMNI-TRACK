# tracker_manager.py

import time
from collections.abc import Awaitable, Callable
from typing import Any


class TrackerState:
    """
    Holds identity resolution state for a single local track_id
    """

    __slots__ = (
        "track_id",
        "global_id",
        "first_seen_ts",
        "last_seen_ts",
        "last_verified_ts",
        "verification_attempts",
    )

    def __init__(self, track_id: str):
        now = time.time()
        self.track_id = track_id
        self.global_id: str | None = None
        self.first_seen_ts = now
        self.last_seen_ts = now
        self.last_verified_ts: float = 0.0
        self.verification_attempts: int = 0


class TrackerManager:
    """
    Distributed identity reconciliation layer

    Works with:
    - YOLO local tracker (track_id)
    - RedisClient (global identity store)
    - Async embedding & similarity services
    """

    def __init__(
        self,
        redis_client: Any,
        embedding_service: Callable[[Any, str], Awaitable[Any]],
        similarity_service: Callable[[Any, Any], Awaitable[tuple[Any, float]]],
        *,
        verification_interval: float = 3.0,
        similarity_threshold: float = 0.80,
        track_timeout: float = 10.0,
        unknown_label: str = "Unknown",
    ) -> None:
        """
        Parameters
        ----------
        redis_client:
            Instance of RedisClient (your wrapper)

        embedding_service:
            ASYNC function:
                embedding = await embedding_service(person_data, track_id)

        similarity_service:
            ASYNC function:
                global_id, similarity = await similarity_service(embedding, redis_client)

        verification_interval:
            Seconds before running global identity verification

        similarity_threshold:
            Minimum similarity required to accept a global ID

        track_timeout:
            Seconds before removing inactive tracks

        unknown_label:
            Label used when no global identity is found
        """
        self._redis = redis_client
        self._embedder = embedding_service
        self._similarity = similarity_service

        self._verification_interval = verification_interval
        self._similarity_threshold = similarity_threshold
        self._track_timeout = track_timeout
        self._unknown_label = unknown_label

        self._tracks: dict[str, TrackerState] = {}

    # -----------------------------
    # Public API
    # -----------------------------

    async def update(self, track_id: str, person_data: Any) -> tuple[str, str]:
        """
        Main entry point called once per detection per frame

        Returns
        -------
        (track_id, global_id)
        """

        now = time.time()
        self._cleanup_expired_tracks(now)

        if track_id not in self._tracks:
            self._tracks[track_id] = TrackerState(track_id)

        state = self._tracks[track_id]
        state.last_seen_ts = now

        # Decide if we should attempt verification
        if self._should_verify(state, now):
            await self._attempt_verification(state, person_data)

        return state.track_id, state.global_id or self._unknown_label

    def format_label(self, track_id: str) -> str:
        """
        Returns formatted label:
            Person_T{track_id}_G{global_id}
        """
        state = self._tracks.get(track_id)

        if not state:
            return f"Person_T{track_id}_G{self._unknown_label}"

        gid = state.global_id or self._unknown_label
        return f"Person_T{track_id}_G{gid}"

    def clear(self) -> None:
        """
        Clears all local tracking state
        """
        self._tracks.clear()

    # -----------------------------
    # Internal Logic
    # -----------------------------

    def _should_verify(self, state: TrackerState, now: float) -> bool:
        """
        Time-based gating logic for embedding verification
        """
        # First-time verification
        if state.last_verified_ts == 0:
            return (now - state.first_seen_ts) >= self._verification_interval

        # Periodic re-verification
        return (now - state.last_verified_ts) >= self._verification_interval

    async def _attempt_verification(self, state: TrackerState, person_data: Any) -> None:
        """
        Generate embedding and attempt Redis-based identity resolution
        """
        try:
            embedding = await self._embedder(person_data, state.track_id)

            global_id, similarity = await self._similarity(embedding, self._redis)

            state.verification_attempts += 1
            state.last_verified_ts = time.time()

            if global_id and similarity >= self._similarity_threshold:
                state.global_id = str(global_id)
            else:
                if state.global_id is None:
                    state.global_id = self._unknown_label

        except Exception as e:
            # Fail-safe: never break pipeline
            print(f"[TrackerManager] Verification failed for T{state.track_id}: {e}")

    def _cleanup_expired_tracks(self, now: float) -> None:
        """
        Remove tracks that haven't been seen recently
        """
        expired = [
            tid
            for tid, state in self._tracks.items()
            if (now - state.last_seen_ts) > self._track_timeout
        ]

        for tid in expired:
            del self._tracks[tid]
