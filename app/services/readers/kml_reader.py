"""Read Placemarks from a KML document.

KML is parsed with ``defusedxml`` (guards against XXE / entity-expansion attacks). Namespaces are
matched by local name so KML 2.0/2.1/2.2 and Google's ``gx:`` extensions are all accepted.
Per the OGC KML spec, coordinates are always WGS84 longitude/latitude (EPSG:4326).
"""

import re
import xml.etree.ElementTree as ET
from typing import BinaryIO

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException
from shapely.geometry import (
    GeometryCollection,
    LinearRing,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.geometry.base import BaseGeometry

from app.core.exceptions import GeoFileParseError
from app.services.crs import WGS84
from app.services.readers.base import ParsedDataset, ParsedFeature

_GEOMETRY_TAGS = {"Point", "LineString", "LinearRing", "Polygon", "MultiGeometry", "Track", "MultiTrack", "Model"}
_CONTAINER_TAGS = {"Document", "Folder"}
_COMMA_SPACING = re.compile(r"\s*,\s*")

Coordinates = list[tuple[float, ...]]


class _KMLGeometryError(ValueError):
    pass


def read_kml(source: BinaryIO) -> ParsedDataset:
    try:
        root = SafeET.parse(source).getroot()
    except DefusedXmlException as exc:
        raise GeoFileParseError("KML file contains forbidden XML constructs (entities/DTD).") from exc
    except ET.ParseError as exc:
        raise GeoFileParseError(f"File is not well-formed XML: {exc}") from exc

    if _local(root.tag) != "kml":
        raise GeoFileParseError("File is not a KML document (missing <kml> root element).")

    dataset = ParsedDataset()
    _walk(root, folder_path=[], dataset=dataset)
    if not dataset.features:
        dataset.warnings.append("KML document contains no Placemarks.")
    return dataset


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _child(element: ET.Element, name: str) -> ET.Element | None:
    return next((c for c in element if _local(c.tag) == name), None)


def _children(element: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in element if _local(c.tag) == name]


def _text(element: ET.Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    return element.text.strip()


def _walk(element: ET.Element, folder_path: list[str], dataset: ParsedDataset) -> None:
    for child in element:
        tag = _local(child.tag)
        if tag == "Placemark":
            dataset.features.append(_parse_placemark(child, folder_path))
        elif tag in _CONTAINER_TAGS:
            name = _text(_child(child, "name"))
            _walk(child, folder_path + [name] if tag == "Folder" and name else folder_path, dataset)


def _parse_placemark(placemark: ET.Element, folder_path: list[str]) -> ParsedFeature:
    properties = _parse_properties(placemark)
    layer = "/".join(folder_path) or None

    geometry_elements = [c for c in placemark if _local(c.tag) in _GEOMETRY_TAGS]
    if not geometry_elements:
        return ParsedFeature(geometry=None, properties=properties, crs=WGS84, layer=layer, error="Placemark has no geometry.")
    try:
        geometry = _parse_geometry(geometry_elements[0])
    except (_KMLGeometryError, ValueError) as exc:
        return ParsedFeature(
            geometry=None, properties=properties, crs=WGS84, layer=layer, error=f"Invalid geometry: {exc}"
        )
    return ParsedFeature(geometry=geometry, properties=properties, crs=WGS84, layer=layer)


def _parse_properties(placemark: ET.Element) -> dict:
    properties: dict = {}
    if (placemark_id := placemark.get("id")) is not None:
        properties["kml_id"] = placemark_id
    for field in ("name", "description"):
        if (value := _text(_child(placemark, field))) is not None:
            properties[field] = value

    extended = _child(placemark, "ExtendedData")
    if extended is not None:
        for data in _children(extended, "Data"):
            if name := data.get("name"):
                properties[name] = _text(_child(data, "value"))
        for schema_data in _children(extended, "SchemaData"):
            for simple in _children(schema_data, "SimpleData"):
                if name := simple.get("name"):
                    properties[name] = _text(simple)
    return properties


def _parse_coordinates(element: ET.Element | None) -> Coordinates:
    text = _text(_child(element, "coordinates")) if element is not None else None
    if not text:
        raise _KMLGeometryError("missing <coordinates>")
    coords = []
    for token in _COMMA_SPACING.sub(",", text).split():
        try:
            values = tuple(float(v) for v in token.split(","))
        except ValueError as exc:
            raise _KMLGeometryError(f"non-numeric coordinate '{token}'") from exc
        if len(values) not in (2, 3):
            raise _KMLGeometryError(f"coordinate '{token}' must have 2 or 3 values")
        coords.append(values)
    return coords


def _parse_ring(boundary: ET.Element | None) -> Coordinates:
    ring = _child(boundary, "LinearRing") if boundary is not None else None
    if ring is None:
        raise _KMLGeometryError("polygon boundary is missing its <LinearRing>")
    coords = _parse_coordinates(ring)
    if len(coords) < 3:
        raise _KMLGeometryError("polygon ring needs at least 3 positions")
    return coords


def _parse_track(track: ET.Element) -> LineString:
    coords = []
    for coord in (c for c in track if _local(c.tag) == "coord"):
        try:
            coords.append(tuple(float(v) for v in (_text(coord) or "").split()))
        except ValueError as exc:
            raise _KMLGeometryError("non-numeric <gx:coord>") from exc
    if len(coords) < 2:
        raise _KMLGeometryError("track needs at least 2 positions")
    return LineString(coords)


def _parse_geometry(element: ET.Element) -> BaseGeometry:
    tag = _local(element.tag)
    if tag == "Point":
        coords = _parse_coordinates(element)
        if len(coords) != 1:
            raise _KMLGeometryError("Point must have exactly one position")
        return Point(coords[0])
    if tag == "LineString":
        coords = _parse_coordinates(element)
        if len(coords) < 2:
            raise _KMLGeometryError("LineString needs at least 2 positions")
        return LineString(coords)
    if tag == "LinearRing":
        coords = _parse_coordinates(element)
        if len(coords) < 3:
            raise _KMLGeometryError("LinearRing needs at least 3 positions")
        return LinearRing(coords)
    if tag == "Polygon":
        shell = _parse_ring(_child(element, "outerBoundaryIs"))
        holes = [_parse_ring(b) for b in _children(element, "innerBoundaryIs")]
        return Polygon(shell, holes)
    if tag == "Track":
        return _parse_track(element)
    if tag == "MultiTrack":
        return MultiLineString([_parse_track(t) for t in _children(element, "Track")])
    if tag == "MultiGeometry":
        return _combine([_parse_geometry(c) for c in element if _local(c.tag) in _GEOMETRY_TAGS])
    raise _KMLGeometryError(f"unsupported KML geometry <{tag}>")


def _combine(parts: list[BaseGeometry]) -> BaseGeometry:
    """Collapse a MultiGeometry into the most specific homogeneous multi-type, else a GeometryCollection."""
    flat: list[BaseGeometry] = []
    for part in parts:
        flat.extend(part.geoms if hasattr(part, "geoms") else [part])
    if not flat:
        return GeometryCollection()

    kinds = {g.geom_type for g in flat}
    if kinds == {"Point"}:
        return MultiPoint(flat)
    if kinds <= {"LineString", "LinearRing"}:
        return MultiLineString([LineString(g.coords) for g in flat])
    if kinds == {"Polygon"}:
        return MultiPolygon(flat)
    return GeometryCollection(flat)
