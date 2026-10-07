"""Builders for in-memory test fixtures (Shapefile zips and KML documents)."""

import io
import zipfile
from collections.abc import Iterable, Sequence
from typing import Any

import shapefile
from pyproj import CRS

KML_NS = "http://www.opengis.net/kml/2.2"


def shapefile_zip(
    shape_type: int,
    records: Iterable[tuple[Any, Sequence[Any]]],
    *,
    fields: Sequence[tuple[str, str]] = (("name", "C"),),
    layer: str = "layer",
    epsg: int | None = 4326,
    include: Sequence[str] = (".shp", ".shx", ".dbf", ".prj"),
    folder: str = "",
    encoding: str = "utf-8",
    cpg: str | None = None,
    extra: dict[str, bytes] | None = None,
) -> bytes:
    """Build a zipped Shapefile. ``records`` is a list of ``(geometry_or_None, attribute_values)``.

    Geometries are given as pyshp-style part lists (polygon rings / line parts) or a single point.
    """
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    writer = shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shape_type, encoding=encoding)
    for name, kind in fields:
        writer.field(name, kind)
    for geometry, values in records:
        if geometry is None:
            writer.null()
        elif shape_type == shapefile.POLYGON:
            writer.poly(geometry)
        elif shape_type == shapefile.POLYLINE:
            writer.line(geometry)
        elif shape_type == shapefile.POINT:
            writer.point(*geometry)
        writer.record(*values)
    writer.close()

    parts = {".shp": shp.getvalue(), ".shx": shx.getvalue(), ".dbf": dbf.getvalue()}
    if epsg is not None:
        parts[".prj"] = CRS.from_epsg(epsg).to_wkt("WKT1_ESRI").encode()
    if cpg is not None:
        parts[".cpg"] = cpg.encode()

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for suffix, data in parts.items():
            if suffix in include or suffix == ".cpg":
                archive.writestr(f"{folder}{layer}{suffix}", data)
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return buffer.getvalue()


def kml_document(placemarks: str, *, namespace: str | None = KML_NS) -> bytes:
    xmlns = f' xmlns="{namespace}" xmlns:gx="http://www.google.com/kml/ext/2.2"' if namespace else ""
    return f'<?xml version="1.0" encoding="UTF-8"?><kml{xmlns}><Document>{placemarks}</Document></kml>'.encode()


def coords(points: Iterable[Sequence[float]]) -> str:
    return " ".join(",".join(str(v) for v in p) for p in points)


def kml_polygon(ring: Sequence[Sequence[float]], name: str = "poly") -> str:
    return (
        f"<Placemark><name>{name}</name><Polygon><outerBoundaryIs><LinearRing>"
        f"<coordinates>{coords(ring)}</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>"
    )


def kml_line(points: Sequence[Sequence[float]], name: str = "line") -> str:
    return f"<Placemark><name>{name}</name><LineString><coordinates>{coords(points)}</coordinates></LineString></Placemark>"


def kml_point(point: Sequence[float], name: str = "point") -> str:
    return f"<Placemark><name>{name}</name><Point><coordinates>{coords([point])}</coordinates></Point></Placemark>"
