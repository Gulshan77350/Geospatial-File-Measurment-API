# Geospatial File Measurement API

A FastAPI service that accepts a **Shapefile (as `.zip`)** or a **KML** file, extracts every feature
(index, geometry type, geometry, CRS, properties), and computes **area** for polygons and **length** for lines.
Measurements are always taken in a **projected, metre-based CRS**, never in lat/lon degrees.

- Pure-Python geospatial stack (`shapely`, `pyproj`, `pyshp`, `defusedxml`). No GDAL install needed.
- Unsupported or broken geometries are reported per feature. They never fail the whole file.
- 61 automated tests, with measurement accuracy checked against ellipsoidal (geodesic) reference values.

---

## Setup

**Requirements:** Python 3.11+ (developed on 3.14).

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate          # Windows (PowerShell): .venv\Scripts\Activate.ps1

# 2. Install dependencies
pip install -r requirements-dev.txt    # or requirements.txt for runtime only

# 3. (Optional) configure: copy and edit the defaults
cp .env.example .env

# 4. Run the API
uvicorn app.main:app --reload
```

- Interactive docs (Swagger UI): <http://127.0.0.1:8000/docs>. ReDoc: <http://127.0.0.1:8000/redoc>
- The SQLite database and stored uploads go to `./data/`, which is created automatically.

**Run the tests:**

```bash
pytest
```

**Try it with the sample files** in [`samples/`](samples/). Regenerate them with `python -m scripts.make_samples`.

```bash
curl -F "file=@samples/survey.kml"  http://127.0.0.1:8000/api/files/
curl -F "file=@samples/parcels.zip" http://127.0.0.1:8000/api/files/
```

### Configuration

All settings are environment variables with the `GEO_` prefix. They can also go in a `.env` file.

| Variable | Default | Purpose |
|---|---|---|
| `GEO_DATABASE_URL` | `sqlite:///./data/app.db` | Any SQLAlchemy URL (e.g. PostgreSQL) |
| `GEO_STORAGE_DIR` | `data/uploads` | Where original uploads are kept |
| `GEO_MAX_UPLOAD_BYTES` | `52428800` (50 MB) | Upload size limit (413 above it) |
| `GEO_MAX_UNCOMPRESSED_BYTES` | `524288000` (500 MB) | Zip-bomb guard for archives |
| `GEO_MAX_ZIP_MEMBERS` | `1000` | Maximum entries in a zip |
| `GEO_DEFAULT_PAGE_SIZE` / `GEO_MAX_PAGE_SIZE` | `100` / `1000` | Pagination for list endpoints |

---

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/files/` | Upload and process a `.zip` (Shapefile) or `.kml` file |
| `GET` | `/api/files/` | List uploaded files, newest first (paginated) |
| `GET` | `/api/files/{id}/` | File information |
| `GET` | `/api/files/{id}/features/` | Extracted features: index, type, GeoJSON geometry, CRS, properties (paginated) |
| `GET` | `/api/files/{id}/measurements/` | Per-feature measurements plus a whole-file summary (paginated) |
| `DELETE` | `/api/files/{id}/` | Delete a file, its features and the stored upload |
| `GET` | `/health` | Liveness check |

Paginated endpoints accept `?limit=` and `?offset=`. Areas are in **m²** and lengths in **m**.

### `POST /api/files/`

Multipart form upload with a single field named `file`.

```bash
curl -F "file=@samples/survey.kml" http://127.0.0.1:8000/api/files/
```

`201 Created`

```json
{
  "id": "c673d03958c94ff7a3d93fb96ba9eac4",
  "filename": "survey.kml",
  "file_type": "KML",
  "size_bytes": 1071,
  "feature_count": 4,
  "crs": "EPSG:4326",
  "status": "COMPLETED",
  "geometry_type_counts": { "Polygon": 1, "LineString": 1, "Point": 1, "GeometryCollection": 1 },
  "warnings": [],
  "error": null,
  "created_at": "2026-10-07T08:54:52.093547Z",
  "processed_at": "2026-10-07T08:54:52.124612Z"
}
```

`warnings` holds non-fatal issues, for example *"Layer 'roads' has no .prj file; coordinates fall within
lon/lat bounds so EPSG:4326 was assumed."* When layers in one zip use different CRSs, `crs` is `"MIXED"`.

### `GET /api/files/{id}/`

Returns the same object as the upload response.

### `GET /api/files/{id}/features/`

```bash
curl "http://127.0.0.1:8000/api/files/c673d03958c94ff7a3d93fb96ba9eac4/features/?limit=1"
```

```json
{
  "file_id": "c673d03958c94ff7a3d93fb96ba9eac4",
  "total": 4,
  "limit": 1,
  "offset": 0,
  "items": [
    {
      "id": 0,
      "layer": "Site survey",
      "geometry_type": "Polygon",
      "crs": "EPSG:4326",
      "geometry": {
        "type": "Polygon",
        "coordinates": [[[77.209, 28.6139, 0.0], [77.219, 28.6139, 0.0], [77.219, 28.6239, 0.0],
                         [77.209, 28.6239, 0.0], [77.209, 28.6139, 0.0]]]
      },
      "properties": { "kml_id": "field-1", "name": "North field", "crop": "wheat" }
    }
  ]
}
```

`geometry` is GeoJSON in the **source** CRS. `layer` is the Shapefile name, or the KML folder path
(e.g. `"Farm/2024"`).

### `GET /api/files/{id}/measurements/`

```bash
curl http://127.0.0.1:8000/api/files/c673d03958c94ff7a3d93fb96ba9eac4/measurements/
```

```json
{
  "file_id": "c673d03958c94ff7a3d93fb96ba9eac4",
  "source_crs": "EPSG:4326",
  "units": { "area": "square_metre", "length": "metre" },
  "summary": {
    "total_features": 4, "measured": 2, "not_applicable": 1, "unsupported": 1, "failed": 0,
    "total_area_m2": 1084234.3074, "total_length_m": 1707.1416
  },
  "total": 4, "limit": 100, "offset": 0,
  "items": [
    { "feature_id": 0, "layer": "Site survey", "geometry_type": "Polygon", "status": "MEASURED",
      "area_m2": 1084234.3074, "perimeter_m": 4173.2171, "length_m": null,
      "measurement_crs": "EPSG:32643", "note": null },
    { "feature_id": 1, "layer": "Site survey", "geometry_type": "LineString", "status": "MEASURED",
      "area_m2": null, "perimeter_m": null, "length_m": 1707.1416,
      "measurement_crs": "EPSG:32643", "note": null },
    { "feature_id": 2, "layer": "Site survey", "geometry_type": "Point", "status": "NOT_APPLICABLE",
      "area_m2": null, "perimeter_m": null, "length_m": null,
      "measurement_crs": null, "note": "Points have no area or length." },
    { "feature_id": 3, "layer": "Site survey", "geometry_type": "GeometryCollection", "status": "UNSUPPORTED",
      "area_m2": null, "perimeter_m": null, "length_m": null,
      "measurement_crs": null, "note": "Measurement is not supported for GeometryCollection." }
  ]
}
```

`summary` always covers the whole file, not only the current page. `measurement_crs` shows which
projected CRS each value was computed in.

| `status` | Meaning |
|---|---|
| `MEASURED` | Polygon/MultiPolygon → `area_m2` + `perimeter_m`. LineString/MultiLineString/LinearRing → `length_m` |
| `NOT_APPLICABLE` | Point/MultiPoint: nothing to measure |
| `UNSUPPORTED` | Geometry type that is not measured (e.g. a mixed `GeometryCollection`) |
| `FAILED` | Missing or invalid geometry, unknown CRS, or a transformation error. `note` gives the reason |

### Errors

Errors use the shape `{"detail": "<message>"}`.

| Code | When |
|---|---|
| `400` | Empty upload or missing filename |
| `404` | Unknown file id |
| `409` | Features or measurements requested for a file whose status is not `COMPLETED` |
| `413` | Upload exceeds `GEO_MAX_UPLOAD_BYTES` |
| `415` | Extension is not `.zip` or `.kml` |
| `422` | The file was stored but could not be parsed. The record is kept with `status: "FAILED"` and returned alongside the error |

Example `422` response:

```json
{
  "detail": "Shapefile 'roads' is missing its .dbf attribute file.",
  "file": { "id": "…", "filename": "roads.zip", "status": "FAILED", "error": "Shapefile 'roads' is missing its .dbf attribute file.", "…": "…" }
}
```

---

## Architecture

### Application structure

```
app/
├── main.py                  # App factory: lifespan (DB/tables), exception handler, routers
├── api/files.py             # HTTP layer only: params, status codes, response models
├── schemas.py               # Pydantic response contracts
├── models.py                # SQLAlchemy ORM: GeoFile 1─* Feature (with measurement columns)
├── core/
│   ├── config.py            # pydantic-settings (GEO_* env vars)
│   ├── database.py          # Engine/session factory, get_db dependency
│   └── exceptions.py        # Domain errors, each carrying its HTTP status
└── services/
    ├── file_service.py      # Orchestration: validate → store → parse → measure → persist
    ├── readers/
    │   ├── base.py          # ParsedFeature / ParsedDataset (format-agnostic)
    │   ├── shapefile_reader.py
    │   └── kml_reader.py
    ├── crs.py               # CRS labelling + projected-CRS selection + transformation
    └── measurement.py       # Per-geometry-type measurement rules
tests/                       # Unit tests (readers, measurement) + API tests
samples/                     # Example .kml and Shapefile .zip
```

Dependencies only point one way: `api → services → (readers, crs, measurement) → models`. The readers
know nothing about HTTP or the database. They return plain `ParsedFeature` objects, so supporting a new
format such as GeoJSON or KMZ only needs a new reader.

### File-processing flow

```
POST /api/files/
  │
  ├─ 1. Validate extension (.zip → SHAPEFILE, .kml → KML), else 415
  ├─ 2. Stream to data/uploads/{id}/{sanitised name} in 1 MB chunks; enforce size limit (413), reject empty (400)
  ├─ 3. Insert GeoFile row with status=PROCESSING
  ├─ 4. Parse with the matching reader → ParsedDataset (features + warnings)
  │     • Shapefile: open zip, group members by path stem (handles sub-folders, skips __MACOSX),
  │       check entry count and uncompressed size, read .shp/.shx/.dbf in memory with pyshp,
  │       CRS from .prj, encoding from .cpg; supports several layers in one zip
  │     • KML: defusedxml parse; walk Document/Folder tree; each Placemark → geometry
  │       (Point, LineString, LinearRing, Polygon with holes, MultiGeometry, gx:Track/MultiTrack)
  │       + properties (name, description, ExtendedData Data/SchemaData)
  │     • A bad geometry is recorded on that feature (geometry=null + reason); the file continues
  ├─ 5. Measure every feature (below) and save Feature rows
  └─ 6. status=COMPLETED (201), or FAILED with an error message (422) if the file as a whole is unreadable
```

### Measurement calculation flow

For each feature, in [`measurement.py`](app/services/measurement.py):

1. No geometry or empty geometry → `FAILED`.
2. Point / MultiPoint → `NOT_APPLICABLE`.
3. Anything other than polygonal or lineal (e.g. `GeometryCollection`) → `UNSUPPORTED`.
4. Unknown CRS → `FAILED`, because the coordinates cannot be safely converted to metres.
5. Drop Z values, then **project** the geometry (see CRS handling).
6. Polygonal: if the projected polygon is invalid (e.g. a self-intersecting "bow-tie"), repair it with
   `make_valid` and add a note. Then `area_m2 = area` and `perimeter_m = boundary length`. Multi-parts are summed.
7. Lineal: `length_m = length` (summed over parts).

Results are rounded to 0.1 mm and stored with the feature. The `GET` endpoints read stored values and
never recompute.

### CRS handling

The CRS is detected per layer:

- **KML:** always EPSG:4326, as the OGC KML specification requires.
- **Shapefile:** read from the `.prj` file. If the `.prj` is missing but every coordinate fits within
  lon/lat bounds, EPSG:4326 is **assumed** and a warning is added. If it is missing and the coordinates
  are clearly not lon/lat, the CRS stays unknown and those features return `FAILED` with an explanation.
  The service does not guess a projected CRS.

The projected CRS for measurement is chosen per feature, in [`crs.py`](app/services/crs.py):

| Source CRS | Measurement CRS |
|---|---|
| Projected, non-Mercator (e.g. British National Grid, a UTM zone, State Plane) | **The source CRS itself.** The data author chose it for that area. Units are converted to metres, so US-feet State Plane data works too |
| Geographic (lon/lat), or Web/World Mercator | Convert to WGS84 lon/lat, then use the feature's **WGS84 UTM zone** (EPSG:326xx north / 327xx south), provided every vertex lies within 4° of the zone's central meridian and between 80°S and 84°N |
| …but too wide for one UTM zone, or polar | Use a projection centred on the feature: **Lambert Azimuthal Equal-Area** for polygons, which preserves area at any size, or **Azimuthal Equidistant** for lines |

Web Mercator (EPSG:3857) is treated like lon/lat input. It is "projected", but at 60°N its areas are
about 4× too large. A test covers this case. Features that cross the antimeridian are handled by
computing their centre in 0–360° longitude space.

---

## Design decisions

| Decision | Alternatives considered | Why |
|---|---|---|
| **FastAPI** | Django + DRF | The service is a small API with no admin or templates. FastAPI provides typed request/response models, automatic OpenAPI docs and dependency injection with less boilerplate |
| **pyshp + defusedxml + shapely + pyproj** (pure Python wheels) | GeoPandas / Fiona / pyogrio (GDAL), GeoDjango | GDAL is hard to install, especially on Windows, and makes the image larger. Whether GDAL supports KML depends on how it was built (KML vs LIBKML drivers). With the pure-Python stack, `pip install` works everywhere, and the code controls exactly how KML maps to features. The trade-off is fewer formats, but adding GDAL later only means adding a reader |
| **Per-feature UTM, with LAEA/AEQD fallback** | One CRS for the whole file; always a local LAEA/AEQD; geodesic calculations (`pyproj.Geod`) | UTM is standard, easy to audit and returns a recognisable EPSG code, with scale error ≤ ~0.2% inside the tolerance used here. Choosing per feature keeps files that span several zones accurate. The fallbacks cover cases where UTM breaks down. The brief requires projection, which rules out geodesic as the main method. Geodesic values are still used in the tests as the reference |
| **Measure natively in a projected source CRS** | Always reproject to UTM | Re-projecting data that is already in a suitable local CRS adds error and can disagree with the client's own GIS. Mercator-family CRSs are the exception because they distort area badly |
| **No guessing of a missing projected CRS** | Assume a default, or guess from coordinate ranges | A wrong guess produces confident but wrong numbers. Only the lon/lat case is safe to infer, and it is flagged as a warning |
| **Synchronous processing in the request** (`def` handler in FastAPI's threadpool) | Background tasks / Celery + `PROCESSING` polling | Typical survey files take milliseconds to process, so the client gets complete results in one call. A `status` field (`PROCESSING`/`COMPLETED`/`FAILED`) is already in the model, so moving to async workers later would not change the API contract |
| **Compute measurements once on upload and persist them** | Compute on every `GET` | Reads are cheap and consistent. Summaries are a single SQL aggregate |
| **SQLite via SQLAlchemy 2.0** | PostgreSQL/PostGIS, in-memory dict | No setup is needed to run it locally. Switching to PostgreSQL only needs `GEO_DATABASE_URL`. Geometry is stored as GeoJSON because the service does not run spatial queries |
| **Per-feature failure instead of whole-file failure** | Reject the whole file | Real-world files often contain a few broken or null geometries. The client still gets every feature, with a machine-readable `status` and a human-readable `note` |
| **Return 422 for unparseable files and keep the FAILED record** | Return 201 with status FAILED, or keep no record | The HTTP status signals the failure clearly, and the stored record supports later inspection or reprocessing |
| **Secure input handling** | Trust the uploaded file | Uploads are size-limited while streaming. Zip contents are read in memory and never extracted to disk, which prevents path traversal ("zip slip"). Zip entry count and uncompressed size are capped (zip bombs). KML is parsed with `defusedxml` (XXE and entity expansion). Stored filenames are sanitised |
| **Domain exceptions carry their HTTP status** | `HTTPException` raised inside services | Services stay independent of the web framework, and one handler maps errors to responses |
| **App factory (`create_app(settings)`)** | Global app and engine | Each test gets an isolated database and storage directory without monkey-patching |

Known trade-offs of the current scope:

- The database schema is created on startup with `create_all`, so there are no migrations yet.
- Very large files are parsed fully in memory and processed inside the request.
- Lines that are both very long and too wide for a UTM zone can carry small scale distortion. The API
  says so in that feature's `note`.

---

## Learnings

- **Coordinates are not measurements.** Lat/lon degrees are angles. One degree of longitude is about 111 km
  at the equator and about 56 km at 60°N, so area and length must be computed in a projected CRS.
  Even then, "projected" is not enough: Web Mercator is projected but inflates areas about 4× at 60°N.
- **Choosing a projection is a trade-off.** UTM is accurate and well known inside its zone but degrades
  outside it. Equal-area projections preserve area but not length. Picking a CRS per feature, with
  fallbacks, gave accurate results across the cases tested.
- **Validate against an independent reference.** Comparing projected results with ellipsoidal (geodesic)
  calculations from `pyproj.Geod` turned "looks plausible" into a measurable accuracy check (within 0.1%).
- **Real-world geospatial files are messy.** Shapefiles arrive in sub-folders, with macOS metadata, without
  a `.prj`, or in non-UTF-8 encodings. KML has several namespace versions, nested folders and
  `MultiGeometry`. Handling failures per feature, instead of rejecting the whole file, made the API
  far more useful.
- **File uploads are an attack surface.** Zip bombs, path traversal in archives and XML entity expansion
  all had to be handled explicitly.
- **Keep the layers separate.** Readers that return plain feature objects, services that know nothing about
  HTTP, and an app factory that takes settings made the code easy to test in isolation.

## Future scope

- **Background processing:** move parsing and measurement to a task queue (Celery/RQ or ARQ) so large
  uploads return immediately with `PROCESSING` and clients poll or receive a webhook. The `status`
  field already supports this.
- **More formats:** KMZ, GeoJSON, GeoPackage and GML, possibly through an optional GDAL/pyogrio reader.
- **PostgreSQL + PostGIS:** store geometries natively, enabling spatial queries (bounding-box filters,
  intersections) and `geography`-type measurements as a cross-check.
- **Database migrations** with Alembic instead of `create_all` on startup.
- **Streaming and bulk inserts** for very large files, instead of holding all features in memory.
- **More measurements and options:** unit selection (ha, km², acres, miles), geodesic values alongside the
  projected ones, centroids and bounding boxes, and measuring the polygon and line parts inside mixed
  `GeometryCollection`s.
- **Export:** download features with their measurements as GeoJSON or CSV.
- **Production readiness:** Dockerfile and docker-compose, authentication and per-user file ownership,
  rate limiting, object storage (S3) for uploads, structured logging and metrics, and CI that runs
  tests and linting on every push.
