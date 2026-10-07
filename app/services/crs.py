"""CRS utilities: labelling, and choosing a projected CRS in which measurements are meaningful.

Strategy (per feature):

1. Source CRS is projected and *not* Mercator-family -> measure in the source CRS (it was chosen by
   the data's author for that area); only convert the linear unit (e.g. US feet) to metres.
2. Otherwise (geographic lon/lat, or Web/World Mercator whose scale error is huge away from the
   equator) -> transform to WGS84 lon/lat, then to a projection local to the feature:
   a. the WGS84 UTM zone containing the feature, if every vertex is within ``UTM_MAX_OFFSET_DEG``
      of that zone's central meridian and inside UTM's latitude band (scale error < ~0.25%);
   b. otherwise (very wide or polar features) a CRS centred on the feature itself: Lambert
      Azimuthal Equal-Area for polygons (area exactly preserved at any size) and Azimuthal
      Equidistant for lines.
"""

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import shapely
from pyproj import CRS, Transformer
from shapely import affinity
from shapely.geometry.base import BaseGeometry

WGS84 = CRS.from_epsg(4326)

UTM_MAX_OFFSET_DEG = 4.0
UTM_MIN_LAT, UTM_MAX_LAT = -80.0, 84.0
LARGE_EXTENT_DEG = 10.0


class MeasurementError(Exception):
    pass


@dataclass(frozen=True)
class ProjectedGeometry:
    geometry: BaseGeometry  # coordinates in metres
    crs_label: str
    note: str | None = None


@lru_cache(maxsize=256)
def crs_label(crs: CRS) -> str:
    """Human-friendly identifier, e.g. ``EPSG:4326``; falls back to the CRS name."""
    authority = crs.to_authority(min_confidence=70)
    return f"{authority[0]}:{authority[1]}" if authority else crs.name


def looks_like_lon_lat(geometry: BaseGeometry) -> bool:
    min_x, min_y, max_x, max_y = geometry.bounds
    return -180.0 <= min_x <= max_x <= 180.0 and -90.0 <= min_y <= max_y <= 90.0


def is_measurable_projection(crs: CRS) -> bool:
    """Projected CRSs are fit for measurement, except the (pseudo-)Mercator family."""
    if not crs.is_projected:
        return False
    operation = crs.coordinate_operation
    method = operation.method_name.lower() if operation else ""
    return not ("mercator" in method and "transverse" not in method)


def project_for_measurement(geometry: BaseGeometry, source_crs: CRS, *, polygonal: bool) -> ProjectedGeometry:
    if is_measurable_projection(source_crs):
        factor = source_crs.axis_info[0].unit_conversion_factor
        if factor != 1.0:
            geometry = affinity.scale(geometry, factor, factor, origin=(0, 0))
        return ProjectedGeometry(geometry, crs_label(source_crs))

    lon_lat = geometry if source_crs == WGS84 else _transform(geometry, source_crs, WGS84)
    target, label, note = _local_crs(lon_lat, polygonal=polygonal)
    return ProjectedGeometry(_transform(lon_lat, WGS84, target), label, note)


def _local_crs(lon_lat: BaseGeometry, *, polygonal: bool) -> tuple[CRS, str, str | None]:
    coords = shapely.get_coordinates(lon_lat)
    lons, lats = coords[:, 0], coords[:, 1]

    # Features crossing the antimeridian: work in 0..360 so the centre is computed correctly.
    if lons.max() - lons.min() > 180:
        lons = np.where(lons < 0, lons + 360, lons)
    lon_c = ((lons.min() + lons.max()) / 2 + 180) % 360 - 180
    lat_c = (lats.min() + lats.max()) / 2

    zone = int((lon_c + 180) // 6) % 60 + 1
    central_meridian = zone * 6 - 183
    max_offset = np.abs((lons - central_meridian + 180) % 360 - 180).max()

    if UTM_MIN_LAT <= lats.min() and lats.max() <= UTM_MAX_LAT and max_offset <= UTM_MAX_OFFSET_DEG:
        epsg = (32600 if lat_c >= 0 else 32700) + zone
        return CRS.from_epsg(epsg), f"EPSG:{epsg}", None

    if polygonal:
        proj, name = "laea", "Lambert Azimuthal Equal-Area"
        note = f"Feature does not fit a single UTM zone; a feature-centred {name} projection was used (area-preserving)."
    else:
        proj, name = "aeqd", "Azimuthal Equidistant"
        note = f"Feature does not fit a single UTM zone; a feature-centred {name} projection was used."
        extent = max(lats.max() - lats.min(), (lons.max() - lons.min()) * np.cos(np.radians(lat_c)))
        if extent > LARGE_EXTENT_DEG:
            note += " The feature is very large, so lengths far from its centre include some scale distortion."

    definition = f"+proj={proj} +lat_0={lat_c:.6f} +lon_0={lon_c:.6f} +datum=WGS84 +units=m +no_defs"
    return CRS.from_proj4(definition), definition, note


@lru_cache(maxsize=256)
def _transformer(source: CRS, target: CRS) -> Transformer:
    return Transformer.from_crs(source, target, always_xy=True)


def _transform(geometry: BaseGeometry, source: CRS, target: CRS) -> BaseGeometry:
    transformer = _transformer(source, target)

    def _apply(xy: np.ndarray) -> np.ndarray:
        x, y = transformer.transform(xy[:, 0], xy[:, 1])
        return np.column_stack([x, y])

    result = shapely.transform(geometry, _apply)
    if not np.isfinite(shapely.get_coordinates(result)).all():
        raise MeasurementError("Coordinates could not be transformed (outside the valid area of the CRS).")
    return result
