"""Format-agnostic types produced by every reader."""

import base64
import datetime as dt
import math
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from pyproj import CRS
from shapely.geometry.base import BaseGeometry


@dataclass
class ParsedFeature:
    geometry: BaseGeometry | None
    properties: dict[str, Any]
    crs: CRS | None
    layer: str | None = None
    # Set when the feature's geometry could not be built; the feature is still reported.
    error: str | None = None


@dataclass
class ParsedDataset:
    features: list[ParsedFeature] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def json_safe(value: Any) -> Any:
    """Coerce attribute values (dates, bytes, NaN, Decimal...) into JSON-serialisable primitives."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (dt.date, dt.datetime, dt.time)):
        return value.isoformat()
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return base64.b64encode(value).decode("ascii")
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return str(value)
