import cv2
import numpy as np

from constants.camera_direction import UP_TO_DOWN
from constants.region_names import BBOX, LINE, POLYGON
from dtos.common.enums.region_type import RegionType
from dtos.request.request_register_use_case import BoundingBoxCoordinates, Point, Region


class RegionDrawer:
    """Interactive region drawing tool for OpenCV windows"""

    def __init__(self, frame: np.ndarray):
        self.frame = frame.copy()
        self.display_frame = frame.copy()
        self.regions: list[Region] = []
        self.current_points: list[tuple[int, int]] = []
        self.drawing = False
        self.current_mode: str | None = None  # 'polygon', 'bbox', 'line'
        self.bbox_start: tuple[int, int] | None = None
        self.region_counter = {POLYGON: 0, BBOX: 0, LINE: 0}

    def mouse_callback(self, event, x, y, _flags, _param):  # noqa: C901
        """Handle mouse events for drawing regions"""

        if self.current_mode == POLYGON:
            if event == cv2.EVENT_LBUTTONDOWN:
                self.current_points.append((x, y))
                cv2.circle(self.display_frame, (x, y), 5, (0, 255, 0), -1)
                if len(self.current_points) > 1:
                    cv2.line(
                        self.display_frame,
                        self.current_points[-2],
                        self.current_points[-1],
                        (0, 255, 0),
                        2,
                    )

            elif event == cv2.EVENT_RBUTTONDOWN and len(self.current_points) >= 3:
                # Close polygon
                cv2.line(
                    self.display_frame,
                    self.current_points[-1],
                    self.current_points[0],
                    (0, 255, 0),
                    2,
                )
                self._save_polygon()

        elif self.current_mode == BBOX:
            if event == cv2.EVENT_LBUTTONDOWN:
                self.bbox_start = (x, y)
                self.drawing = True

            elif event == cv2.EVENT_MOUSEMOVE and self.drawing and self.bbox_start is not None:
                temp_frame = self.display_frame.copy()
                cv2.rectangle(temp_frame, self.bbox_start, (x, y), (255, 0, 0), 2)
                cv2.imshow("Region Drawing", temp_frame)

            elif event == cv2.EVENT_LBUTTONUP and self.drawing and self.bbox_start is not None:
                self.drawing = False
                cv2.rectangle(self.display_frame, self.bbox_start, (x, y), (255, 0, 0), 2)
                self._save_bbox(self.bbox_start, (x, y))

        elif self.current_mode == LINE:
            if event == cv2.EVENT_LBUTTONDOWN:
                self.current_points.append((x, y))
                cv2.circle(self.display_frame, (x, y), 5, (0, 0, 255), -1)
                if len(self.current_points) == 2:
                    cv2.line(
                        self.display_frame,
                        self.current_points[0],
                        self.current_points[1],
                        (0, 0, 255),
                        2,
                    )
                    self._save_line()

    def _save_polygon(self):
        """Save current polygon to regions list"""
        self.region_counter[POLYGON] += 1
        points = [Point(x=int(x), y=int(y)) for x, y in self.current_points]
        print(f"{self.current_points}")
        region = Region(
            type=RegionType.POLYGON, name=f"polygon_{self.region_counter[POLYGON]}", points=points
        )
        self.regions.append(region)
        self.current_points = []
        print(f"✓ Polygon saved: {region.name} ({len(points)} points)")

    def _save_bbox(self, start, end):
        """Save bounding box to regions list"""
        self.region_counter["bbox"] += 1
        x_min = min(start[0], end[0])
        x_max = max(start[0], end[0])
        y_min = min(start[1], end[1])
        y_max = max(start[1], end[1])
        print(f"bbox->x_min={x_min},x_max={x_max},y_min={y_min},y_max={y_max}")

        region = Region(
            type=RegionType.BOUNDING_BOX,
            name=f"bbox_{self.region_counter[BBOX]}",
            coordinates=BoundingBoxCoordinates(x_min=x_min, y_min=y_min, x_max=x_max, y_max=y_max),
        )
        self.regions.append(region)
        self.bbox_start = None
        print(f"✓ Bounding box saved: {region.name}")

    def _save_line(self):
        """Save line to regions list"""
        self.region_counter[LINE] += 1
        line_points = [Point(x=int(x), y=int(y)) for x, y in self.current_points]
        region = Region(
            type=RegionType.Line,
            name=f"line_{self.region_counter[LINE]}",
            line_points=line_points,
            object_moving_direction=UP_TO_DOWN,
        )
        self.regions.append(region)
        self.current_points = []
        print(f"✓ Line saved: {region.name}")

    def draw_regions(self):
        """Interactively draw regions on the frame"""
        cv2.namedWindow("Region Drawing")
        cv2.setMouseCallback("Region Drawing", self.mouse_callback)

        print("\n=== Region Drawing Tool ===")
        print("Controls:")
        print("  'p' - Start drawing POLYGON (left-click points, right-click to close)")
        print("  'b' - Start drawing BOUNDING BOX (click and drag)")
        print("  'l' - Start drawing LINE (click 2 points)")
        print("  'c' - Clear current drawing")
        print("  'r' - Reset all regions")
        print("  'Enter' - Finish and continue")
        print("  'q' - Quit\n")

        while True:
            cv2.imshow("Region Drawing", self.display_frame)
            key = cv2.waitKey(1) & 0xFF

            if key == ord("p"):
                self.current_mode = POLYGON
                self.current_points = []
                print("Mode: POLYGON - Click to add points, right-click to close")

            elif key == ord("b"):
                self.current_mode = BBOX
                print("Mode: BOUNDING BOX - Click and drag to draw")

            elif key == ord("l"):
                self.current_mode = LINE
                self.current_points = []
                print("Mode: LINE - Click 2 points")

            elif key == ord("c"):
                # Clear current drawing
                self.current_points = []
                self.bbox_start = None
                self.display_frame = self.frame.copy()
                self._redraw_saved_regions()
                print("Cleared current drawing")

            elif key == ord("r"):
                # Reset all
                self.regions = []
                self.current_points = []
                self.bbox_start = None
                self.display_frame = self.frame.copy()
                self.region_counter = {POLYGON: 0, BBOX: 0, LINE: 0}
                print("Reset all regions")

            elif key == 13:  # Enter
                print(f"\n✓ Finished drawing. Total regions: {len(self.regions)}")
                cv2.destroyWindow("Region Drawing")
                return self.regions

            elif key == ord("q"):
                cv2.destroyWindow("Region Drawing")
                return None

    def _redraw_saved_regions(self):
        """Redraw all saved regions on the frame"""
        for region in self.regions:
            if region.type == RegionType.POLYGON:
                poly_points = region.points
                if not poly_points:
                    continue
                pts = np.array([[p.x, p.y] for p in poly_points], np.int32)
                cv2.polylines(self.display_frame, [pts], True, (0, 255, 0), 2)
            elif region.type == RegionType.BOUNDING_BOX:
                bbox = region.coordinates
                if bbox is None:
                    continue
                cv2.rectangle(
                    self.display_frame,
                    (bbox.x_min, bbox.y_min),
                    (bbox.x_max, bbox.y_max),
                    (255, 0, 0),
                    2,
                )
            elif region.type == RegionType.Line:
                line_pts = region.line_points
                if line_pts is None or len(line_pts) < 2:
                    continue
                pt1 = (line_pts[0].x, line_pts[0].y)
                pt2 = (line_pts[1].x, line_pts[1].y)
                cv2.line(self.display_frame, pt1, pt2, (0, 0, 255), 2)
