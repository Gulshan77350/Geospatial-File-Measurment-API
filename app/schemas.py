"""Response schemas (the API's public contract)."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models import FileStatus, FileType, MeasurementStatus


class FileInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    file_type: FileType
    size_bytes: int
    feature_count: int
    crs: str | None = Field(description="CRS of the source data, e.g. 'EPSG:4326'; 'MIXED' if layers differ.")
    status: FileStatus
    geometry_type_counts: dict[str, int]
    warnings: list[str]
    error: str | None
    created_at: datetime
    processed_at: datetime | None


class FileList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[FileInfo]


class FailedUpload(BaseModel):
    detail: str
    file: FileInfo


class FeatureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(validation_alias="index", description="Zero-based index of the feature within the file.")
    layer: str | None = Field(description="Shapefile name, or KML folder path.")
    geometry_type: str | None
    crs: str | None
    geometry: dict[str, Any] | None = Field(description="GeoJSON geometry in the source CRS.")
    properties: dict[str, Any]


class FeatureList(BaseModel):
    file_id: str
    total: int
    limit: int
    offset: int
    items: list[FeatureOut]


class MeasurementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    feature_id: int = Field(validation_alias="index")
    layer: str | None
    geometry_type: str | None
    status: MeasurementStatus = Field(validation_alias="measurement_status")
    area_m2: float | None
    perimeter_m: float | None
    length_m: float | None
    measurement_crs: str | None = Field(description="Projected CRS the measurement was computed in.")
    note: str | None = Field(validation_alias="measurement_note")


class MeasurementSummary(BaseModel):
    total_features: int
    measured: int
    not_applicable: int
    unsupported: int
    failed: int
    total_area_m2: float
    total_length_m: float


class MeasurementList(BaseModel):
    file_id: str
    source_crs: str | None
    units: dict[str, str] = {"area": "square_metre", "length": "metre"}
    summary: MeasurementSummary
    total: int
    limit: int
    offset: int
    items: list[MeasurementOut]


class ErrorResponse(BaseModel):
    detail: str
