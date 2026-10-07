"""Read one or more Shapefiles from a ``.zip`` archive using pyshp (pure Python, no GDAL needed)."""

import codecs
import io
import zipfile
from collections import defaultdict
from pathlib import PurePosixPath
from typing import BinaryIO

import shapefile
from pyproj import CRS
from pyproj.exceptions import CRSError
from shapely.geometry import shape as to_shapely

from app.core.exceptions import GeoFileParseError
from app.services.crs import WGS84, looks_like_lon_lat
from app.services.readers.base import ParsedDataset, ParsedFeature, json_safe

_COMPONENTS = {".shp", ".shx", ".dbf", ".prj", ".cpg"}


def read_shapefile_zip(
    source: BinaryIO,
    *,
    max_uncompressed_bytes: int,
    max_members: int,
) -> ParsedDataset:
    try:
        archive = zipfile.ZipFile(source)
    except zipfile.BadZipFile as exc:
        raise GeoFileParseError("Uploaded file is not a valid ZIP archive.") from exc

    with archive:
        layers = _group_components(archive, max_uncompressed_bytes=max_uncompressed_bytes, max_members=max_members)
        if not layers:
            raise GeoFileParseError("ZIP archive does not contain a Shapefile (.shp).")

        dataset = ParsedDataset()
        budget = [max_uncompressed_bytes]
        for _key, members in sorted(layers.items()):
            _read_layer(archive, members, dataset, budget)
        return dataset


def _group_components(
    archive: zipfile.ZipFile, *, max_uncompressed_bytes: int, max_members: int
) -> dict[str, dict[str, zipfile.ZipInfo]]:
    """Group archive members into layers keyed by path-without-extension.

    Members are never extracted to disk, so path traversal ("zip slip") is not a concern; the
    declared sizes are checked here and the actual bytes read are checked again in ``_read_member``.
    """
    infos = archive.infolist()
    if len(infos) > max_members:
        raise GeoFileParseError(f"ZIP archive has too many entries ({len(infos)} > {max_members}).")
    if sum(info.file_size for info in infos) > max_uncompressed_bytes:
        raise GeoFileParseError("ZIP archive is too large when uncompressed.")

    layers: dict[str, dict[str, zipfile.ZipInfo]] = defaultdict(dict)
    for info in infos:
        path = PurePosixPath(info.filename.replace("\\", "/"))
        if info.is_dir() or "__MACOSX" in path.parts or path.name.startswith("._"):
            continue
        suffix = path.suffix.lower()
        if suffix in _COMPONENTS:
            layers[str(path.with_suffix("")).lower()][suffix] = info

    return {key: members for key, members in layers.items() if ".shp" in members}


def _read_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo, budget: list[int]) -> bytes:
    with archive.open(info) as handle:
        data = handle.read(budget[0] + 1)
    budget[0] -= len(data)
    if budget[0] < 0:
        raise GeoFileParseError("ZIP archive is too large when uncompressed.")
    return data


def _read_layer(
    archive: zipfile.ZipFile,
    members: dict[str, zipfile.ZipInfo],
    dataset: ParsedDataset,
    budget: list[int],
) -> None:
    layer_name = PurePosixPath(members[".shp"].filename.replace("\\", "/")).stem
    if ".dbf" not in members:
        raise GeoFileParseError(f"Shapefile '{layer_name}' is missing its .dbf attribute file.")

    crs = _read_crs(archive, members, budget, layer_name, dataset.warnings)
    encoding = _read_encoding(archive, members, budget)

    try:
        reader = shapefile.Reader(
            shp=io.BytesIO(_read_member(archive, members[".shp"], budget)),
            dbf=io.BytesIO(_read_member(archive, members[".dbf"], budget)),
            shx=io.BytesIO(_read_member(archive, members[".shx"], budget)) if ".shx" in members else None,
            encoding=encoding,
            encodingErrors="replace",
        )
        features = [_to_feature(record, layer_name) for record in reader.iterShapeRecords()]
    except GeoFileParseError:
        raise
    except Exception as exc:  # pyshp raises a variety of errors for corrupt files
        raise GeoFileParseError(f"Could not read Shapefile '{layer_name}': {exc}") from exc

    if crs is None:
        crs = _assume_crs(features, layer_name, dataset.warnings)
    for feature in features:
        feature.crs = crs
    dataset.features.extend(features)


def _to_feature(shape_record: shapefile.ShapeRecord, layer_name: str) -> ParsedFeature:
    properties = {name: json_safe(value) for name, value in shape_record.record.as_dict().items()}
    shp = shape_record.shape
    if shp.shapeType == shapefile.NULL:
        return ParsedFeature(geometry=None, properties=properties, crs=None, layer=layer_name, error="Null geometry.")
    try:
        geometry = to_shapely(shp.__geo_interface__)
    except Exception as exc:
        return ParsedFeature(
            geometry=None, properties=properties, crs=None, layer=layer_name, error=f"Invalid geometry: {exc}"
        )
    return ParsedFeature(geometry=geometry, properties=properties, crs=None, layer=layer_name)


def _read_crs(
    archive: zipfile.ZipFile,
    members: dict[str, zipfile.ZipInfo],
    budget: list[int],
    layer_name: str,
    warnings: list[str],
) -> CRS | None:
    if ".prj" not in members:
        return None
    wkt = _read_member(archive, members[".prj"], budget).decode("utf-8", errors="replace").strip()
    try:
        return CRS.from_wkt(wkt)
    except CRSError:
        warnings.append(f"Layer '{layer_name}': .prj file could not be parsed; CRS treated as missing.")
        return None


def _read_encoding(archive: zipfile.ZipFile, members: dict[str, zipfile.ZipInfo], budget: list[int]) -> str:
    """Use the .cpg code page if present and recognised, otherwise UTF-8."""
    if ".cpg" in members:
        name = _read_member(archive, members[".cpg"], budget).decode("ascii", errors="ignore").strip()
        if name.isdigit():  # e.g. "1252" -> "cp1252"
            name = f"cp{name}"
        try:
            return codecs.lookup(name).name
        except LookupError:
            pass
    return "utf-8"


def _assume_crs(features: list[ParsedFeature], layer_name: str, warnings: list[str]) -> CRS | None:
    """Without a .prj, coordinates are only usable if they are unambiguously lon/lat degrees."""
    geometries = [f.geometry for f in features if f.geometry is not None and not f.geometry.is_empty]
    if geometries and all(looks_like_lon_lat(g) for g in geometries):
        warnings.append(
            f"Layer '{layer_name}' has no .prj file; coordinates fall within lon/lat bounds so EPSG:4326 was assumed."
        )
        return WGS84
    warnings.append(f"Layer '{layer_name}' has no .prj file and its CRS could not be inferred; it cannot be measured.")
    return None
