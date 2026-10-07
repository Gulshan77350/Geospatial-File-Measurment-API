"""Generate the sample files in ``samples/`` (run: python -m scripts.make_samples)."""

import datetime
from pathlib import Path

import shapefile

from tests.helpers import kml_document, shapefile_zip

SAMPLES = Path(__file__).resolve().parent.parent / "samples"

KML_BODY = """
<Folder><name>Site survey</name>
  <Placemark id="field-1"><name>North field</name>
    <ExtendedData><Data name="crop"><value>wheat</value></Data></ExtendedData>
    <Polygon><outerBoundaryIs><LinearRing><coordinates>
      77.2090,28.6139,0 77.2190,28.6139,0 77.2190,28.6239,0 77.2090,28.6239,0 77.2090,28.6139,0
    </coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
  <Placemark id="road-1"><name>Access road</name>
    <LineString><coordinates>77.2090,28.6139 77.2150,28.6100 77.2250,28.6100</coordinates></LineString></Placemark>
  <Placemark id="well-1"><name>Well</name><Point><coordinates>77.2140,28.6189</coordinates></Point></Placemark>
  <Placemark id="mixed-1"><name>Mixed</name><MultiGeometry>
    <Point><coordinates>77.2100,28.6150</coordinates></Point>
    <LineString><coordinates>77.2100,28.6150 77.2120,28.6170</coordinates></LineString>
  </MultiGeometry></Placemark>
</Folder>
"""


def main() -> None:
    SAMPLES.mkdir(exist_ok=True)
    (SAMPLES / "survey.kml").write_bytes(kml_document(KML_BODY))

    parcels = [
        ([[(-0.1300, 51.5000), (-0.1300, 51.5050), (-0.1220, 51.5050), (-0.1220, 51.5000), (-0.1300, 51.5000)]],
         ["P-001", "residential", datetime.date(2024, 5, 14)]),
        ([[(-0.1200, 51.5000), (-0.1200, 51.5030), (-0.1150, 51.5030), (-0.1150, 51.5000), (-0.1200, 51.5000)]],
         ["P-002", "commercial", datetime.date(2024, 6, 2)]),
    ]
    (SAMPLES / "parcels.zip").write_bytes(
        shapefile_zip(
            shapefile.POLYGON,
            parcels,
            fields=[("parcel_id", "C"), ("land_use", "C"), ("surveyed", "D")],
            layer="parcels",
            epsg=4326,
        )
    )


if __name__ == "__main__":
    main()
