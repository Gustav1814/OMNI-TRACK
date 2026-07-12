import hashlib
import random


class ColorManager:
    def __init__(self):
        self._tracked_colors: dict[str, tuple[int, int, int]] = {}
        from constants.color import COLORS

        self._color_palette = COLORS

    def get_color(
        self, track_id: str | None = None, class_id: int | None = None
    ) -> tuple[int, int, int]:
        """Get a color for the object. Uses track_id for consistent colors if tracking, else random."""
        if track_id is not None:
            # Use deterministic color for tracked objects
            if track_id not in self._tracked_colors:
                # Generate a color based on track_id (deterministic)
                color_index = int(hashlib.md5(str(track_id).encode()).hexdigest(), 16) % len(
                    self._color_palette
                )
                self._tracked_colors[track_id] = self._color_palette[color_index]
            return self._tracked_colors[track_id]
        else:
            # Use random color for non-tracked objects
            return (
                random.choice(self._color_palette)
                if class_id is None
                else self._color_palette[class_id % len(self._color_palette)]
            )

    def clear_tracked_colors(self):
        """Clear stored colors for tracked objects (e.g., when processing a new video)."""
        self._tracked_colors.clear()
