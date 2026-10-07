"""HTTP layer for /api/files. Handlers are plain ``def`` so blocking file/CPU work runs in FastAPI's threadpool."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import get_db
from app.models import FileStatus
from app.schemas import (
    ErrorResponse,
    FailedUpload,
    FeatureList,
    FeatureOut,
    FileInfo,
    FileList,
    MeasurementList,
    MeasurementOut,
    MeasurementSummary,
)
from app.services import file_service

router = APIRouter(prefix="/api/files", tags=["files"])

DB = Annotated[Session, Depends(get_db)]


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


SettingsDep = Annotated[Settings, Depends(get_settings)]


class Page:
    """Shared ``limit``/``offset`` query parameters, bounded by settings."""

    def __init__(
        self,
        settings: SettingsDep,
        limit: Annotated[int | None, Query(ge=1, description="Page size.")] = None,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> None:
        self.limit = min(limit or settings.default_page_size, settings.max_page_size)
        self.offset = offset


PageDep = Annotated[Page, Depends()]

_NOT_FOUND = {404: {"model": ErrorResponse}}
_NOT_READY = {409: {"model": ErrorResponse, "description": "File did not finish processing successfully."}}


@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=FileInfo,
    summary="Upload and process a geospatial file (.zip Shapefile or .kml)",
    responses={
        400: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
        415: {"model": ErrorResponse},
        422: {"model": FailedUpload, "description": "File was stored but could not be parsed."},
    },
)
def upload_file(
    db: DB,
    settings: SettingsDep,
    file: Annotated[UploadFile, File(description="A .zip containing a Shapefile, or a .kml file.")],
):
    record = file_service.create_file(db, settings, file.filename, file.file)
    info = FileInfo.model_validate(record)
    if record.status == FileStatus.FAILED:
        body = FailedUpload(detail=record.error or "Processing failed.", file=info)
        return JSONResponse(status_code=422, content=body.model_dump(mode="json"))
    return info


@router.get("/", response_model=FileList, summary="List uploaded files (newest first)")
def list_files(db: DB, page: PageDep) -> FileList:
    total, records = file_service.list_files(db, limit=page.limit, offset=page.offset)
    return FileList(
        total=total,
        limit=page.limit,
        offset=page.offset,
        items=[FileInfo.model_validate(r) for r in records],
    )


@router.get("/{file_id}/", response_model=FileInfo, summary="Get file information", responses=_NOT_FOUND)
def get_file(file_id: str, db: DB) -> FileInfo:
    return FileInfo.model_validate(file_service.get_file(db, file_id))


@router.get(
    "/{file_id}/features/",
    response_model=FeatureList,
    summary="List extracted features (geometry, CRS, properties)",
    responses=_NOT_FOUND | _NOT_READY,
)
def list_features(file_id: str, db: DB, page: PageDep) -> FeatureList:
    record = file_service.get_completed_file(db, file_id)
    features = file_service.list_features(db, file_id, limit=page.limit, offset=page.offset)
    return FeatureList(
        file_id=file_id,
        total=record.feature_count,
        limit=page.limit,
        offset=page.offset,
        items=[FeatureOut.model_validate(f) for f in features],
    )


@router.get(
    "/{file_id}/measurements/",
    response_model=MeasurementList,
    summary="Get per-feature measurements (area / length in metres)",
    responses=_NOT_FOUND | _NOT_READY,
)
def list_measurements(file_id: str, db: DB, page: PageDep) -> MeasurementList:
    record = file_service.get_completed_file(db, file_id)
    features = file_service.list_features(db, file_id, limit=page.limit, offset=page.offset)
    return MeasurementList(
        file_id=file_id,
        source_crs=record.crs,
        summary=MeasurementSummary(**file_service.measurement_summary(db, file_id)),
        total=record.feature_count,
        limit=page.limit,
        offset=page.offset,
        items=[MeasurementOut.model_validate(f) for f in features],
    )


@router.delete(
    "/{file_id}/",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a file, its features and the stored upload",
    responses=_NOT_FOUND,
)
def delete_file(file_id: str, db: DB, settings: SettingsDep) -> Response:
    file_service.delete_file(db, settings, file_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
