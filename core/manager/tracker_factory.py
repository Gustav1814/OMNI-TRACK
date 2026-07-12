# services/trackers/tracker_factory.py
from typing import Any, cast

from config.BaseConfig import config
from services.trackers.global_base_tracker import BaseGlobalTracker
from services.trackers.interface.itracker import ITracker
from services.trackers.yolo_trackers import (
    BoostTrackTracker,
    BoTSORTTracker,
    ByteTracker,
    CustomTracker,
    DeepSORTTracker,
    HybridSORTTracker,
    OCSORTTracker,
)


class TrackerFactory:
    """
    Factory for creating tracker instances.
    Supports easy registration of new tracker types.
    """

    _trackers: dict[str, type] = {
        "bytetrack": ByteTracker,
        "byte": ByteTracker,  # Alias
        "botsort": BoTSORTTracker,
        "bot": BoTSORTTracker,  # Alias
        "ocsort": OCSORTTracker,
        "hybridsort": HybridSORTTracker,
        "deepsort": DeepSORTTracker,
        "boosttrack": BoostTrackTracker,
        "global_base": BaseGlobalTracker,
        "custom": CustomTracker,
    }

    @classmethod
    def create_tracker(
        cls, tracker_name: str | None = None, tracker_config: dict[str, Any] | None = None
    ) -> ITracker:
        """
        Create a tracker instance by name.

        Args:
            tracker_name: Name of the tracker ("bytetrack", "botsort", "deepsort", "custom")
            tracker_config: Optional configuration dictionary for custom trackers

        Returns:
            Tracker instance implementing ITracker

        Raises:
            ValueError: If tracker_name is not recognized

        Examples:
            >>> tracker = TrackerFactory.create_tracker("bytetrack")
            >>> tracker = TrackerFactory.create_tracker("custom", {"param": "value"})
            >>> tracker = TrackerFactory.create_tracker("ocsort")
        """
        if tracker_name is None:
            tracker_name = config.FALLBACK_TRACKER

        tracker_name_lower = tracker_name.lower().strip()

        if tracker_name_lower not in cls._trackers:
            print(
                f"Warning: Tracker '{tracker_name}' not present. Falling back to '{config.FALLBACK_TRACKER}'."
            )
            tracker_name_lower = config.FALLBACK_TRACKER.lower().strip()

        tracker_class = cls._trackers[tracker_name_lower]

        # CustomTracker accepts config, others don't (global_base accepts config too)
        if tracker_name_lower == "global_base" or tracker_name_lower == "custom":
            return cast("ITracker", tracker_class(tracker_config=tracker_config))
        return cast("ITracker", tracker_class())

    @classmethod
    def register_tracker(cls, name: str, tracker_class: type) -> None:
        """
        Register a new tracker type.

        Args:
            name: Name to register the tracker under
            tracker_class: Class implementing ITracker protocol

        Example:
            >>> class MyTracker(BaseTracker):
            ...     pass
            >>> TrackerFactory.register_tracker("mytracker", MyTracker)
        """
        cls._trackers[name.lower()] = tracker_class

    @classmethod
    def get_available_trackers(cls) -> list[str]:
        """
        Get list of available tracker names.

        Returns:
            List of registered tracker names
        """
        return list(cls._trackers.keys())
