# common/enums/region_type.py
from enum import StrEnum


class RegionType(StrEnum):
    POLYGON = "polygon"
    BOUNDING_BOX = "bounding_box"
    Line = "Line"
