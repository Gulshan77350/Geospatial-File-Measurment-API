"""Per-feature measurement: area/perimeter for polygons, length for lines, nothing for points."""

from dataclasses import dataclass

import shapely
from pyproj import CRS
from pyproj.exceptions import CRSError, ProjError
from shapely.geometry import MultiPolygon
from shapely.geometry.base import BaseGeometry

from app.models import MeasurementStatus
from app.services.crs import MeasurementError, project_for_measurement

POINT_TYPES = {"Point", "MultiPoint"}
POLYGON_TYPES = {"Polygon", "MultiPolygon"}
LINE_TYPES = {"LineString", "MultiLineString", "LinearRing"}

_DECIMALS = 4


@dataclass
class MeasurementResult:
    status: MeasurementStatus
    area_m2: float | None = None
    perimeter_m: float | None = None
    length_m: float | None = None
    crs: str | None = None
    note: str | None = None


def measure(geometry: BaseGeometry | None, crs: CRS | None) -> MeasurementResult:
    if geometry is None:
        return MeasurementResult(MeasurementStatus.FAILED, note="Feature has no geometry.")
    if geometry.is_empty:
        return MeasurementResult(MeasurementStatus.FAILED, note="Geometry is empty.")

    geometry_type = geometry.geom_type
    if geometry_type in POINT_TYPES:
        return MeasurementResult(MeasurementStatus.NOT_APPLICABLE, note="Points have no area or length.")
    if geometry_type not in POLYGON_TYPES | LINE_TYPES:
        return MeasurementResult(
            MeasurementStatus.UNSUPPORTED, note=f"Measurement is not supported for {geometry_type}."
        )
    if crs is None:
        return MeasurementResult(
            MeasurementStatus.FAILED, note="CRS is unknown, so the geometry cannot be projected for measurement."
        )

    polygonal = geometry_type in POLYGON_TYPES
    try:
        projected = project_for_measurement(shapely.force_2d(geometry), crs, polygonal=polygonal)
    except MeasurementError as exc:
        return MeasurementResult(MeasurementStatus.FAILED, note=str(exc))
    except (CRSError, ProjError) as exc:
        return MeasurementResult(MeasurementStatus.FAILED, note=f"CRS transformation failed: {exc}")

    notes = [projected.note] if projected.note else []
    result = MeasurementResult(MeasurementStatus.MEASURED, crs=projected.crs_label)
    shape = projected.geometry

    if polygonal:
        if not shape.is_valid:
            shape = _polygonal_part(shapely.make_valid(shape))
            notes.append("Geometry was invalid (e.g. self-intersecting) and was repaired before measuring.")
        result.area_m2 = round(shape.area, _DECIMALS)
        result.perimeter_m = round(shape.length, _DECIMALS)
    else:
        result.length_m = round(shape.length, _DECIMALS)

    result.note = " ".join(notes) or None
    return result


def _polygonal_part(geometry: BaseGeometry) -> MultiPolygon:
    """``make_valid`` can return a collection mixing polygons with lines/points; keep only the area."""
    polygons = []
    for part in shapely.get_parts(geometry):
        if part.geom_type == "Polygon":
            polygons.append(part)
        elif part.geom_type == "MultiPolygon":
            polygons.extend(part.geoms)
    return MultiPolygon(polygons)
