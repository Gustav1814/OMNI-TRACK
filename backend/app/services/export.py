"""
OmniTrack AI — Data Export Service
Generate CSV/JSON reports for analytics data.
Managers love spreadsheets — this gives them what they want.
"""

import csv
import json
import io
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta
from loguru import logger


class ExportService:
    """
    Export analytics data in CSV or JSON format.
    
    Supports export of:
      - Detection logs
      - Foot traffic reports
      - Demographics summaries
      - Emotion/sentiment trends
      - Checkout performance
      - Vibe score history
      - Audit trail
    """

    @staticmethod
    def to_csv(headers: List[str], rows: List[List[Any]], filename: str = "export") -> dict:
        """
        Generate a CSV string from headers and row data.
        Returns dict with filename and content.
        """
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(headers)
        writer.writerows(rows)
        return {
            "filename": f"{filename}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            "content": output.getvalue(),
            "content_type": "text/csv",
            "row_count": len(rows),
        }

    @staticmethod
    def to_json(data: Any, filename: str = "export") -> dict:
        """
        Generate a JSON export.
        Returns dict with filename and content.
        """
        content = json.dumps(data, indent=2, default=str)
        return {
            "filename": f"{filename}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
            "content": content,
            "content_type": "application/json",
        }

    @staticmethod
    def detection_report(detections: List[Dict]) -> dict:
        """Export detection data as CSV."""
        headers = ["ID", "Camera", "Timestamp", "Class", "Confidence", "Track ID", "Zone",
                    "BBox X", "BBox Y", "BBox W", "BBox H"]
        rows = []
        for d in detections:
            rows.append([
                d.get("id", ""), d.get("camera_id", ""),
                d.get("timestamp", ""), d.get("class_name", "person"),
                round(d.get("confidence", 0), 3), d.get("track_id", ""),
                d.get("zone", ""),
                d.get("bbox_x", ""), d.get("bbox_y", ""),
                d.get("bbox_w", ""), d.get("bbox_h", ""),
            ])
        return ExportService.to_csv(headers, rows, "detection_report")

    @staticmethod
    def traffic_report(traffic_data: List[Dict]) -> dict:
        """Export foot traffic data as CSV."""
        headers = ["Date", "Hour", "Zone", "Person Count", "Direction In",
                    "Direction Out", "Net Flow"]
        rows = []
        for t in traffic_data:
            d_in = t.get("direction_in", 0)
            d_out = t.get("direction_out", 0)
            rows.append([
                t.get("date", ""), t.get("hour", ""),
                t.get("zone", ""), t.get("person_count", 0),
                d_in, d_out, d_in - d_out,
            ])
        return ExportService.to_csv(headers, rows, "foot_traffic_report")

    @staticmethod
    def demographics_report(demo_data: List[Dict]) -> dict:
        """
        Export demographics data as CSV.

        One row is one VISITOR: `Faces` is how many face reads were averaged
        into it, and a blank Age Group means the face was read but too small
        or too turned away to call.
        """
        headers = ["Date", "Zone", "Camera", "Age Group", "Gender",
                   "Mood", "Faces", "Count"]
        rows = [[d.get("date", ""), d.get("zone", ""), d.get("camera_id", ""),
                 d.get("age_group", ""), d.get("gender", ""),
                 d.get("dominant_emotion", ""), d.get("sample_count", ""),
                 d.get("count", 0)] for d in demo_data]
        return ExportService.to_csv(headers, rows, "demographics_report")

    @staticmethod
    def checkout_report(checkout_data: List[Dict]) -> dict:
        """Export checkout performance data as CSV."""
        headers = ["Lane", "Timestamp", "Queue Length", "Avg Service Time (s)",
                    "Throughput/hr", "Wait Estimate (s)"]
        rows = []
        for c in checkout_data:
            rows.append([
                c.get("lane_id", ""), c.get("timestamp", ""),
                c.get("queue_length", 0), round(c.get("avg_service_time", 0), 1),
                round(c.get("throughput", 0), 1), round(c.get("current_wait_estimate", 0), 0),
            ])
        return ExportService.to_csv(headers, rows, "checkout_report")

    @staticmethod
    def vibe_history_report(vibe_data: List[Dict]) -> dict:
        """Export Store Vibe Score history as CSV."""
        headers = ["Timestamp", "Overall Score", "Sentiment", "Energy",
                    "Engagement", "Foot Traffic", "Label"]
        rows = []
        for v in vibe_data:
            rows.append([
                v.get("timestamp", ""), round(v.get("overall_score", 0), 1),
                round(v.get("sentiment_score", 0), 1),
                round(v.get("energy_score", 0), 1),
                round(v.get("engagement_score", 0), 1),
                round(v.get("foot_traffic_score", 0), 1),
                v.get("vibe_label", ""),
            ])
        return ExportService.to_csv(headers, rows, "vibe_history_report")

    @staticmethod
    def audit_report(audit_logs: List[Dict]) -> dict:
        """Export audit trail as CSV (sensitive: encrypted metadata excluded)."""
        headers = ["ID", "Event Type", "User ID", "Description", "Timestamp",
                    "Hash", "Previous Hash", "Chain Valid"]
        rows = []
        for a in audit_logs:
            rows.append([
                a.get("id", ""), a.get("event_type", ""),
                a.get("user_id", ""), a.get("description", ""),
                a.get("timestamp", ""),
                a.get("current_hash", "")[:16] + "...",  # Truncate hash for readability
                (a.get("previous_hash", "") or "GENESIS")[:16] + "...",
                a.get("is_valid", True),
            ])
        return ExportService.to_csv(headers, rows, "audit_trail_report")

    @staticmethod
    def line_passing_report(rows: List[Dict]) -> dict:
        """
        Export line-crossing counts as CSV.

        One row per track per line, carrying that track's cumulative counters at
        the moment it was last written. Summing a column across tracks gives the
        line total; summing across ROWS would double-count, because the counters
        are cumulative rather than incremental.
        """
        headers = ["Job", "Camera", "Line", "Track ID", "Global ID", "Class",
                   "In", "Out", "Left", "Right", "Crossed", "First Seen In",
                   "Timestamp (us)", "Recorded At"]
        rows_out = []
        for r in rows:
            rows_out.append([
                r.get("job_id", ""), r.get("camera_id", ""),
                r.get("region_name", ""), r.get("track_id", ""),
                r.get("global_id", ""), r.get("class_name", ""),
                r.get("in_count", 0), r.get("out_count", 0),
                r.get("left_count", 0), r.get("right_count", 0),
                r.get("is_line_crossed", False),
                r.get("first_seen_in_region", ""),
                r.get("at_us", ""), r.get("created_at", ""),
            ])
        return ExportService.to_csv(headers, rows_out, "line_passing_report")

    @staticmethod
    def roi_dwell_report(rows: List[Dict]) -> dict:
        """Export ROI dwell times as CSV. Durations are converted us -> seconds."""
        headers = ["Job", "Camera", "Region", "Track ID", "Global ID", "Class",
                   "Current Dwell (s)", "Total Dwell (s)", "Visited Regions",
                   "Timestamp (us)", "Recorded At"]
        rows_out = []
        for r in rows:
            cur = (r.get("current_region_dwell_us") or 0) / 1_000_000.0
            tot = (r.get("total_dwell_us") or 0) / 1_000_000.0
            rows_out.append([
                r.get("job_id", ""), r.get("camera_id", ""),
                r.get("region_name", ""), r.get("track_id", ""),
                r.get("global_id", ""), r.get("class_name", ""),
                round(cur, 2), round(tot, 2),
                r.get("visited_regions", ""),
                r.get("at_us", ""), r.get("created_at", ""),
            ])
        return ExportService.to_csv(headers, rows_out, "roi_dwell_report")

    @staticmethod
    def alerts_report(rows: List[Dict]) -> dict:
        """Export job alerts as CSV."""
        headers = ["Job", "Camera", "Type", "Region", "Track ID", "Class",
                   "Value", "Threshold", "Message", "Timestamp (us)", "Recorded At"]
        rows_out = []
        for r in rows:
            rows_out.append([
                r.get("job_id", ""), r.get("camera_id", ""),
                r.get("alert_type", ""), r.get("region_name", ""),
                r.get("track_id", ""), r.get("class_name", ""),
                r.get("value", ""), r.get("threshold", ""),
                r.get("message", ""), r.get("at_us", ""), r.get("created_at", ""),
            ])
        return ExportService.to_csv(headers, rows_out, "alerts_report")

    @staticmethod
    def journeys_report(rows: List[Dict]) -> dict:
        """Export cross-camera customer journeys as CSV."""
        headers = ["Global ID", "Camera", "Zone", "Dwell (s)", "Zones Visited",
                   "Entry Time", "Exit Time", "Total Duration (s)"]
        rows_out = []
        for r in rows:
            rows_out.append([
                r.get("global_id", ""), r.get("camera_id", ""), r.get("zone", ""),
                round(r.get("dwell_time") or 0, 2), r.get("zones_visited", 0),
                r.get("entry_time", ""), r.get("exit_time", ""),
                round(r.get("total_duration") or 0, 2),
            ])
        return ExportService.to_csv(headers, rows_out, "customer_journeys_report")

    @staticmethod
    def full_store_report(
        detections: List[Dict],
        traffic: List[Dict],
        demographics: List[Dict],
        vibe_data: List[Dict],
    ) -> dict:
        """
        Generate a comprehensive store analytics JSON report.
        This is the "give me everything" export for management.
        """
        report = {
            "report_type": "Full Store Analytics",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "total_detections": len(detections),
                "traffic_entries": len(traffic),
                "demographic_samples": len(demographics),
                "vibe_readings": len(vibe_data),
            },
            "detections": detections,
            "foot_traffic": traffic,
            "demographics": demographics,
            "vibe_history": vibe_data,
        }
        return ExportService.to_json(report, "full_store_report")
