import pytest
import shapefile

from tests.helpers import kml_document, kml_line, kml_point, kml_polygon, shapefile_zip

SQUARE_LL = [(77.0, 28.0), (77.0, 28.01), (77.01, 28.01), (77.01, 28.0), (77.0, 28.0)]


def upload(client, filename: str, content: bytes):
    return client.post("/api/files/", files={"file": (filename, content, "application/octet-stream")})


@pytest.fixture
def kml_file(client) -> dict:
    data = kml_document(
        kml_polygon(SQUARE_LL, "field")
        + kml_line([(77.0, 28.0), (77.01, 28.0)], "road")
        + kml_point((77.0, 28.0), "well")
        + "<Placemark><name>bad</name><LineString><coordinates>1,1</coordinates></LineString></Placemark>"
    )
    response = upload(client, "survey.kml", data)
    assert response.status_code == 201, response.text
    return response.json()


class TestUpload:
    def test_kml_upload_returns_file_info(self, kml_file):
        assert kml_file["filename"] == "survey.kml"
        assert kml_file["file_type"] == "KML"
        assert kml_file["status"] == "COMPLETED"
        assert kml_file["feature_count"] == 4
        assert kml_file["crs"] == "EPSG:4326"
        assert kml_file["geometry_type_counts"] == {"Polygon": 1, "LineString": 1, "Point": 1, "None": 1}

    def test_shapefile_upload(self, client):
        data = shapefile_zip(shapefile.POLYGON, [([SQUARE_LL], ["plot"])], layer="parcels")
        body = upload(client, "parcels.zip", data).json()
        assert body["status"] == "COMPLETED"
        assert body["file_type"] == "SHAPEFILE"
        assert body["crs"] == "EPSG:4326"

        measurements = client.get(f"/api/files/{body['id']}/measurements/").json()
        item = measurements["items"][0]
        assert item["layer"] == "parcels"
        assert item["measurement_crs"] == "EPSG:32643"
        assert item["area_m2"] == pytest.approx(1.0896e6, rel=2e-3)

    def test_unsupported_extension(self, client):
        response = upload(client, "data.geojson", b"{}")
        assert response.status_code == 415
        assert ".zip, .kml" in response.json()["detail"]

    def test_empty_file(self, client):
        assert upload(client, "empty.kml", b"").status_code == 400

    def test_file_too_large(self, client, settings):
        response = upload(client, "big.kml", b"x" * (settings.max_upload_bytes + 1))
        assert response.status_code == 413
        assert not any(settings.storage_dir.iterdir())  # partial upload cleaned up

    def test_unparseable_file_is_recorded_as_failed(self, client):
        response = upload(client, "broken.zip", b"definitely not a zip")
        assert response.status_code == 422
        body = response.json()
        assert body["file"]["status"] == "FAILED"
        assert body["detail"] == body["file"]["error"]

        file_id = body["file"]["id"]
        assert client.get(f"/api/files/{file_id}/").json()["status"] == "FAILED"
        assert client.get(f"/api/files/{file_id}/measurements/").status_code == 409

    def test_filename_path_components_are_stripped(self, client):
        body = upload(client, "../../evil.kml", kml_document(kml_point((1, 1)))).json()
        assert body["filename"] == "evil.kml"


class TestRetrieval:
    def test_get_file(self, client, kml_file):
        response = client.get(f"/api/files/{kml_file['id']}/")
        assert response.status_code == 200
        assert response.json() == kml_file

    def test_unknown_file_returns_404(self, client):
        for path in ("/api/files/nope/", "/api/files/nope/features/", "/api/files/nope/measurements/"):
            assert client.get(path).status_code == 404

    def test_features(self, client, kml_file):
        body = client.get(f"/api/files/{kml_file['id']}/features/").json()
        assert body["total"] == 4
        polygon = body["items"][0]
        assert polygon["id"] == 0
        assert polygon["geometry_type"] == "Polygon"
        assert polygon["crs"] == "EPSG:4326"
        assert polygon["geometry"]["type"] == "Polygon"
        assert polygon["properties"] == {"name": "field"}

    def test_measurements(self, client, kml_file):
        body = client.get(f"/api/files/{kml_file['id']}/measurements/").json()
        assert body["source_crs"] == "EPSG:4326"
        assert body["units"] == {"area": "square_metre", "length": "metre"}
        assert body["summary"]["measured"] == 2
        assert body["summary"]["not_applicable"] == 1
        assert body["summary"]["failed"] == 1

        polygon, line, point, bad = body["items"]
        assert polygon["status"] == "MEASURED" and polygon["area_m2"] > 1e6
        assert line["status"] == "MEASURED" and line["length_m"] == pytest.approx(983.0, rel=2e-3)
        assert point["status"] == "NOT_APPLICABLE"
        assert bad["status"] == "FAILED" and "at least 2 positions" in bad["note"]
        assert body["summary"]["total_area_m2"] == polygon["area_m2"]

    def test_pagination(self, client, kml_file):
        body = client.get(f"/api/files/{kml_file['id']}/measurements/?limit=2&offset=1").json()
        assert [i["feature_id"] for i in body["items"]] == [1, 2]
        assert body["summary"]["total_features"] == 4  # summary covers the whole file

    def test_page_size_is_capped(self, client, kml_file, settings):
        body = client.get(f"/api/files/{kml_file['id']}/features/?limit=100000").json()
        assert body["limit"] == settings.max_page_size

    def test_list_files(self, client, kml_file):
        body = client.get("/api/files/").json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == kml_file["id"]

    def test_delete(self, client, kml_file, settings):
        assert client.delete(f"/api/files/{kml_file['id']}/").status_code == 204
        assert client.get(f"/api/files/{kml_file['id']}/").status_code == 404
        assert not (settings.storage_dir / kml_file["id"]).exists()

    def test_health(self, client):
        assert client.get("/health").json() == {"status": "ok"}
