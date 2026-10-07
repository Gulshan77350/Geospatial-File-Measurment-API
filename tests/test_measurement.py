"""Measurement accuracy is checked against pyproj's geodesic (ellipsoidal) calculations."""

import pytest
import numpy as np
import shapely
from pyproj import CRS, Geod, Transformer
from shapely.geometry import GeometryCollection, LineString, MultiPolygon, Point, Polygon, box

from app.models import MeasurementStatus
from app.services.crs import WGS84
from app.services.measurement import measure

GEOD = Geod(ellps="WGS84")


def geodesic_area(geometry) -> float:
    return abs(GEOD.geometry_area_perimeter(geometry)[0])


def geodesic_length(geometry) -> float:
    return GEOD.geometry_length(geometry)


def assert_close(actual: float, expected: float, rel: float = 1e-3) -> None:
    assert actual == pytest.approx(expected, rel=rel)


def to_crs(geometry, epsg: int):
    transformer = Transformer.from_crs(WGS84, CRS.from_epsg(epsg), always_xy=True)
    return shapely.transform(geometry, lambda xy: np.column_stack(transformer.transform(xy[:, 0], xy[:, 1])))


class TestGeographicInput:
    def test_small_polygon_uses_utm_and_matches_geodesic_area(self):
        polygon = box(77.0, 28.0, 77.01, 28.01)
        result = measure(polygon, WGS84)
        assert result.status == MeasurementStatus.MEASURED
        assert result.crs == "EPSG:32643"
        assert_close(result.area_m2, geodesic_area(polygon))
        assert_close(result.perimeter_m, GEOD.geometry_area_perimeter(polygon)[1])
        assert result.length_m is None

    def test_line_length_matches_geodesic(self):
        line = LineString([(-0.1278, 51.5074), (-0.0900, 51.5200), (-0.0500, 51.5000)])
        result = measure(line, WGS84)
        assert result.crs == "EPSG:32630"
        assert_close(result.length_m, geodesic_length(line))

    def test_southern_hemisphere_utm_zone(self):
        result = measure(box(151.20, -33.87, 151.21, -33.86), WGS84)
        assert result.crs == "EPSG:32756"

    def test_multipolygon_area_is_summed(self):
        parts = MultiPolygon([box(10.0, 45.0, 10.01, 45.01), box(10.02, 45.0, 10.03, 45.01)])
        result = measure(parts, WGS84)
        assert_close(result.area_m2, geodesic_area(parts))

    def test_wide_polygon_falls_back_to_equal_area_projection(self):
        polygon = shapely.segmentize(box(0.0, 40.0, 20.0, 50.0), 0.1)
        result = measure(polygon, WGS84)
        assert "+proj=laea" in result.crs
        assert "Lambert Azimuthal Equal-Area" in result.note
        assert_close(result.area_m2, geodesic_area(polygon))

    def test_polar_polygon_is_measured(self):
        polygon = shapely.segmentize(box(-30.0, 85.0, 30.0, 87.0), 0.1)
        result = measure(polygon, WGS84)
        assert "+proj=laea" in result.crs
        assert_close(result.area_m2, geodesic_area(polygon))

    def test_polygon_crossing_antimeridian(self):
        polygon = Polygon([(179.99, -16.0), (-179.99, -16.0), (-179.99, -16.01), (179.99, -16.01)])
        result = measure(polygon, WGS84)
        assert result.status == MeasurementStatus.MEASURED
        # 0.02 deg of longitude x 0.01 deg of latitude near -16 => roughly 2.1 km x 1.1 km
        assert 2.0e6 < result.area_m2 < 2.6e6

    def test_z_coordinates_are_ignored(self):
        flat = box(77.0, 28.0, 77.01, 28.01)
        raised = shapely.force_3d(flat, 500)
        assert measure(raised, WGS84).area_m2 == pytest.approx(measure(flat, WGS84).area_m2)


class TestProjectedInput:
    def test_metric_projected_crs_is_used_natively(self):
        square = box(500_000, 200_000, 500_100, 200_100)  # 100 m x 100 m in British National Grid
        result = measure(square, CRS.from_epsg(27700))
        assert result.crs == "EPSG:27700"
        assert result.area_m2 == pytest.approx(10_000)
        assert result.perimeter_m == pytest.approx(400)

    def test_feet_based_crs_is_converted_to_metres(self):
        square = box(1_000_000, 200_000, 1_001_000, 201_000)  # 1000 US survey ft square (NY Long Island)
        result = measure(square, CRS.from_epsg(2263))
        assert result.area_m2 == pytest.approx((1000 * 1200 / 3937) ** 2, rel=1e-6)

    def test_web_mercator_is_reprojected_instead_of_measured_natively(self):
        polygon_ll = box(10.0, 60.0, 10.01, 60.01)
        polygon_3857 = to_crs(polygon_ll, 3857)
        naive_area = polygon_3857.area  # ~4x too large at 60 degrees north
        result = measure(polygon_3857, CRS.from_epsg(3857))
        assert result.crs.startswith("EPSG:326")
        assert_close(result.area_m2, geodesic_area(polygon_ll))
        assert naive_area > 3 * result.area_m2


class TestGracefulHandling:
    def test_point_is_not_applicable(self):
        result = measure(Point(1, 1), WGS84)
        assert result.status == MeasurementStatus.NOT_APPLICABLE
        assert result.area_m2 is None and result.length_m is None

    def test_geometry_collection_is_unsupported(self):
        result = measure(GeometryCollection([Point(1, 1), LineString([(1, 1), (2, 2)])]), WGS84)
        assert result.status == MeasurementStatus.UNSUPPORTED
        assert "GeometryCollection" in result.note

    def test_missing_geometry_fails(self):
        assert measure(None, WGS84).status == MeasurementStatus.FAILED

    def test_empty_geometry_fails(self):
        assert measure(Polygon(), WGS84).status == MeasurementStatus.FAILED

    def test_unknown_crs_fails(self):
        result = measure(box(0, 0, 1, 1), None)
        assert result.status == MeasurementStatus.FAILED
        assert "CRS is unknown" in result.note

    def test_self_intersecting_polygon_is_repaired(self):
        bow_tie = Polygon([(77.0, 28.0), (77.01, 28.01), (77.01, 28.0), (77.0, 28.01)])
        result = measure(bow_tie, WGS84)
        assert result.status == MeasurementStatus.MEASURED
        assert "repaired" in result.note
        assert_close(result.area_m2, geodesic_area(box(77.0, 28.0, 77.01, 28.01)) / 2, rel=5e-3)

    def test_untransformable_coordinates_fail(self):
        result = measure(box(5_000_000, 5_000_000, 5_000_100, 5_000_100), WGS84)
        assert result.status == MeasurementStatus.FAILED
