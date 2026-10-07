import datetime
import io

import pytest
import shapefile

from app.core.exceptions import GeoFileParseError
from app.services.crs import crs_label
from app.services.readers import read_kml, read_shapefile_zip
from tests.helpers import kml_document, kml_line, kml_point, kml_polygon, shapefile_zip

SQUARE = [[(77.0, 28.0), (77.0, 28.01), (77.01, 28.01), (77.01, 28.0), (77.0, 28.0)]]
LIMITS = {"max_uncompressed_bytes": 10 * 1024 * 1024, "max_members": 100}


def read_zip(data: bytes):
    return read_shapefile_zip(io.BytesIO(data), **LIMITS)


class TestShapefileReader:
    def test_reads_features_crs_and_properties(self):
        data = shapefile_zip(
            shapefile.POLYGON,
            [(SQUARE, ["plot A", 12, datetime.date(2024, 3, 1)]), (None, ["empty", 0, None])],
            fields=[("name", "C"), ("count", "N"), ("surveyed", "D")],
        )
        dataset = read_zip(data)

        first, second = dataset.features
        assert first.geometry.geom_type == "Polygon"
        assert crs_label(first.crs) == "EPSG:4326"
        assert first.layer == "layer"
        assert first.properties == {"name": "plot A", "count": 12, "surveyed": "2024-03-01"}
        assert second.geometry is None and second.error == "Null geometry."

    def test_missing_prj_with_lon_lat_coordinates_assumes_wgs84(self):
        dataset = read_zip(shapefile_zip(shapefile.POLYGON, [(SQUARE, ["a"])], epsg=None))
        assert crs_label(dataset.features[0].crs) == "EPSG:4326"
        assert "EPSG:4326 was assumed" in dataset.warnings[0]

    def test_missing_prj_with_projected_coordinates_leaves_crs_unknown(self):
        ring = [[(500000, 4000000), (500000, 4000100), (500100, 4000100), (500000, 4000000)]]
        dataset = read_zip(shapefile_zip(shapefile.POLYGON, [(ring, ["a"])], epsg=None))
        assert dataset.features[0].crs is None
        assert "cannot be measured" in dataset.warnings[0]

    def test_projected_prj_is_detected(self):
        ring = [[(500000, 200000), (500000, 200100), (500100, 200100), (500000, 200000)]]
        dataset = read_zip(shapefile_zip(shapefile.POLYGON, [(ring, ["a"])], epsg=27700))
        assert crs_label(dataset.features[0].crs) == "EPSG:27700"

    def test_nested_folder_and_macos_metadata_are_handled(self):
        data = shapefile_zip(
            shapefile.POINT,
            [((77.0, 28.0), ["well"])],
            folder="export/data/",
            extra={"__MACOSX/export/data/._layer.shp": b"junk", "readme.txt": b"hi"},
        )
        dataset = read_zip(data)
        assert len(dataset.features) == 1
        assert dataset.features[0].geometry.geom_type == "Point"

    def test_cpg_encoding_is_respected(self):
        data = shapefile_zip(shapefile.POINT, [((10.0, 50.0), ["Müller"])], encoding="latin-1", cpg="ISO-8859-1")
        assert read_zip(data).features[0].properties["name"] == "Müller"

    def test_lines_are_read(self):
        data = shapefile_zip(shapefile.POLYLINE, [([[(0, 0), (1, 1)], [(2, 2), (3, 3)]], ["road"])])
        assert read_zip(data).features[0].geometry.geom_type == "MultiLineString"

    @pytest.mark.parametrize(
        ("data", "message"),
        [
            (b"not a zip", "not a valid ZIP"),
            (shapefile_zip(shapefile.POINT, [((0, 0), ["a"])], include=(".dbf", ".prj")), "does not contain"),
            (shapefile_zip(shapefile.POINT, [((0, 0), ["a"])], include=(".shp", ".shx")), "missing its .dbf"),
        ],
    )
    def test_invalid_archives_raise(self, data, message):
        with pytest.raises(GeoFileParseError, match=message):
            read_zip(data)

    def test_zip_member_limit(self):
        data = shapefile_zip(shapefile.POINT, [((0, 0), ["a"])])
        with pytest.raises(GeoFileParseError, match="too many entries"):
            read_shapefile_zip(io.BytesIO(data), max_uncompressed_bytes=10**7, max_members=2)

    def test_uncompressed_size_limit(self):
        data = shapefile_zip(shapefile.POINT, [((0, 0), ["a"])])
        with pytest.raises(GeoFileParseError, match="too large"):
            read_shapefile_zip(io.BytesIO(data), max_uncompressed_bytes=50, max_members=100)


class TestKMLReader:
    def test_reads_basic_geometries_with_wgs84_crs(self):
        data = kml_document(
            kml_polygon([(0, 0), (0, 1), (1, 1), (0, 0)]) + kml_line([(0, 0), (1, 1)]) + kml_point((5, 5, 10))
        )
        dataset = read_kml(io.BytesIO(data))
        assert [f.geometry.geom_type for f in dataset.features] == ["Polygon", "LineString", "Point"]
        assert all(crs_label(f.crs) == "EPSG:4326" for f in dataset.features)
        assert dataset.features[2].geometry.has_z

    def test_works_without_namespace(self):
        dataset = read_kml(io.BytesIO(kml_document(kml_point((1, 2)), namespace=None)))
        assert dataset.features[0].geometry.coords[0] == (1.0, 2.0)

    def test_properties_and_folder_layers(self):
        placemark = (
            '<Placemark id="pm-7"><name>Field</name><description>North plot</description>'
            '<ExtendedData><Data name="owner"><value>Ann</value></Data>'
            '<SchemaData schemaUrl="#s"><SimpleData name="crop">wheat</SimpleData></SchemaData></ExtendedData>'
            "<Point><coordinates>1,2</coordinates></Point></Placemark>"
        )
        data = kml_document(f"<Folder><name>Farm</name><Folder><name>2024</name>{placemark}</Folder></Folder>")
        feature = read_kml(io.BytesIO(data)).features[0]
        assert feature.layer == "Farm/2024"
        assert feature.properties == {
            "kml_id": "pm-7",
            "name": "Field",
            "description": "North plot",
            "owner": "Ann",
            "crop": "wheat",
        }

    def test_polygon_with_hole(self):
        placemark = (
            "<Placemark><Polygon>"
            "<outerBoundaryIs><LinearRing><coordinates>0,0 0,10 10,10 10,0 0,0</coordinates></LinearRing></outerBoundaryIs>"
            "<innerBoundaryIs><LinearRing><coordinates>2,2 2,4 4,4 4,2 2,2</coordinates></LinearRing></innerBoundaryIs>"
            "</Polygon></Placemark>"
        )
        geometry = read_kml(io.BytesIO(kml_document(placemark))).features[0].geometry
        assert len(geometry.interiors) == 1
        assert geometry.area == pytest.approx(96)

    @pytest.mark.parametrize(
        ("inner", "expected"),
        [
            ("<Point><coordinates>1,1</coordinates></Point><Point><coordinates>2,2</coordinates></Point>", "MultiPoint"),
            (
                "<LineString><coordinates>0,0 1,1</coordinates></LineString>"
                "<LineString><coordinates>2,2 3,3</coordinates></LineString>",
                "MultiLineString",
            ),
            (
                "<Polygon><outerBoundaryIs><LinearRing><coordinates>0,0 0,1 1,1 0,0</coordinates></LinearRing>"
                "</outerBoundaryIs></Polygon>"
                "<Polygon><outerBoundaryIs><LinearRing><coordinates>5,5 5,6 6,6 5,5</coordinates></LinearRing>"
                "</outerBoundaryIs></Polygon>",
                "MultiPolygon",
            ),
            (
                "<Point><coordinates>1,1</coordinates></Point><LineString><coordinates>0,0 1,1</coordinates></LineString>",
                "GeometryCollection",
            ),
        ],
    )
    def test_multigeometry_is_collapsed_to_the_most_specific_type(self, inner, expected):
        data = kml_document(f"<Placemark><MultiGeometry>{inner}</MultiGeometry></Placemark>")
        assert read_kml(io.BytesIO(data)).features[0].geometry.geom_type == expected

    def test_gx_track_becomes_linestring(self):
        placemark = "<Placemark><gx:Track><gx:coord>0 0 0</gx:coord><gx:coord>1 1 0</gx:coord></gx:Track></Placemark>"
        assert read_kml(io.BytesIO(kml_document(placemark))).features[0].geometry.geom_type == "LineString"

    def test_coordinates_with_spaces_after_commas(self):
        data = kml_document("<Placemark><LineString><coordinates>0, 0  1, 1</coordinates></LineString></Placemark>")
        assert len(read_kml(io.BytesIO(data)).features[0].geometry.coords) == 2

    def test_invalid_geometry_is_reported_per_feature(self):
        data = kml_document(
            "<Placemark><LineString><coordinates>0,0</coordinates></LineString></Placemark>"
            "<Placemark><name>no geometry</name></Placemark>" + kml_point((1, 1))
        )
        bad, missing, good = read_kml(io.BytesIO(data)).features
        assert bad.geometry is None and "at least 2 positions" in bad.error
        assert missing.geometry is None and missing.error == "Placemark has no geometry."
        assert good.geometry is not None

    def test_malformed_xml_raises(self):
        with pytest.raises(GeoFileParseError, match="well-formed"):
            read_kml(io.BytesIO(b"<kml><Document>"))

    def test_non_kml_root_raises(self):
        with pytest.raises(GeoFileParseError, match="not a KML document"):
            read_kml(io.BytesIO(b"<gpx></gpx>"))

    def test_entity_expansion_is_rejected(self):
        payload = b'<?xml version="1.0"?><!DOCTYPE kml [<!ENTITY a "aaaa">]><kml><Document>&a;</Document></kml>'
        with pytest.raises(GeoFileParseError, match="forbidden"):
            read_kml(io.BytesIO(payload))

    def test_empty_document_warns(self):
        assert read_kml(io.BytesIO(kml_document(""))).warnings == ["KML document contains no Placemarks."]
