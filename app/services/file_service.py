"""Upload orchestration: validate -> store -> parse -> measure -> persist."""

import logging
import re
import shutil
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from shapely.geometry import mapping
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import (
    FileNotReadyError,
    FileTooLargeError,
    GeoFileParseError,
    InvalidUploadError,
    NotFoundError,
    UnsupportedFileTypeError,
)
from app.models import Feature, FileStatus, FileType, GeoFile, MeasurementStatus
from app.services.crs import crs_label
from app.services.measurement import measure
from app.services.readers import ParsedDataset, read_kml, read_shapefile_zip

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS: dict[str, FileType] = {".zip": FileType.SHAPEFILE, ".kml": FileType.KML}
_CHUNK_SIZE = 1024 * 1024
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def detect_file_type(filename: str) -> FileType:
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        allowed = ", ".join(SUPPORTED_EXTENSIONS)
        raise UnsupportedFileTypeError(f"Unsupported file type '{suffix or filename}'. Allowed: {allowed}.")
    return SUPPORTED_EXTENSIONS[suffix]


def create_file(db: Session, settings: Settings, filename: str | None, stream: BinaryIO) -> GeoFile:
    """Store an upload and process it synchronously. Returns the record (status COMPLETED or FAILED)."""
    if not filename:
        raise InvalidUploadError("Uploaded file must have a filename.")
    original_name = Path(filename.replace("\\", "/")).name
    file_type = detect_file_type(original_name)

    file_id = uuid.uuid4().hex
    directory = settings.storage_dir / file_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (_UNSAFE_CHARS.sub("_", original_name) or "upload")
    try:
        size = _save_stream(stream, path, settings.max_upload_bytes)
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise

    record = GeoFile(
        id=file_id,
        filename=original_name,
        file_type=file_type,
        size_bytes=size,
        storage_path=str(path),
        status=FileStatus.PROCESSING,
    )
    db.add(record)
    db.commit()

    process_file(db, record, settings)
    return record


def process_file(db: Session, record: GeoFile, settings: Settings) -> None:
    try:
        dataset = _parse(record, settings)
        _store_features(record, dataset)
        record.status = FileStatus.COMPLETED
        record.error = None
    except GeoFileParseError as exc:
        db.rollback()
        record.status, record.error = FileStatus.FAILED, exc.message
    except Exception:
        logger.exception("Unexpected error while processing file %s", record.id)
        db.rollback()
        record.status, record.error = FileStatus.FAILED, "Unexpected error while processing the file."

    record.processed_at = datetime.now(UTC)
    db.add(record)
    db.commit()


def get_file(db: Session, file_id: str) -> GeoFile:
    record = db.get(GeoFile, file_id)
    if record is None:
        raise NotFoundError(f"File '{file_id}' not found.")
    return record


def get_completed_file(db: Session, file_id: str) -> GeoFile:
    record = get_file(db, file_id)
    if record.status != FileStatus.COMPLETED:
        raise FileNotReadyError(f"File '{file_id}' has status {record.status.value}; no features are available.")
    return record


def list_files(db: Session, *, limit: int, offset: int) -> tuple[int, list[GeoFile]]:
    total = db.scalar(select(func.count()).select_from(GeoFile)) or 0
    rows = db.scalars(select(GeoFile).order_by(GeoFile.created_at.desc()).offset(offset).limit(limit)).all()
    return total, list(rows)


def list_features(db: Session, file_id: str, *, limit: int, offset: int) -> list[Feature]:
    query = select(Feature).where(Feature.file_id == file_id).order_by(Feature.index).offset(offset).limit(limit)
    return list(db.scalars(query).all())


def measurement_summary(db: Session, file_id: str) -> dict:
    """Aggregate over *all* features of the file (independent of pagination)."""
    def status_count(status: MeasurementStatus):
        return func.sum(case((Feature.measurement_status == status, 1), else_=0))

    row = db.execute(
        select(
            func.count(Feature.id),
            status_count(MeasurementStatus.MEASURED),
            status_count(MeasurementStatus.NOT_APPLICABLE),
            status_count(MeasurementStatus.UNSUPPORTED),
            status_count(MeasurementStatus.FAILED),
            func.coalesce(func.sum(Feature.area_m2), 0.0),
            func.coalesce(func.sum(Feature.length_m), 0.0),
        ).where(Feature.file_id == file_id)
    ).one()
    total, measured, not_applicable, unsupported, failed, area, length = row
    return {
        "total_features": total,
        "measured": measured or 0,
        "not_applicable": not_applicable or 0,
        "unsupported": unsupported or 0,
        "failed": failed or 0,
        "total_area_m2": round(area, 4),
        "total_length_m": round(length, 4),
    }


def delete_file(db: Session, settings: Settings, file_id: str) -> None:
    record = get_file(db, file_id)
    db.delete(record)
    db.commit()
    shutil.rmtree(settings.storage_dir / file_id, ignore_errors=True)


def _save_stream(stream: BinaryIO, path: Path, max_bytes: int) -> int:
    size = 0
    with path.open("wb") as out:
        while chunk := stream.read(_CHUNK_SIZE):
            size += len(chunk)
            if size > max_bytes:
                raise FileTooLargeError(f"File exceeds the maximum upload size of {max_bytes} bytes.")
            out.write(chunk)
    if size == 0:
        raise InvalidUploadError("Uploaded file is empty.")
    return size


def _parse(record: GeoFile, settings: Settings) -> ParsedDataset:
    with open(record.storage_path, "rb") as handle:
        if record.file_type == FileType.KML:
            return read_kml(handle)
        return read_shapefile_zip(
            handle,
            max_uncompressed_bytes=settings.max_uncompressed_bytes,
            max_members=settings.max_zip_members,
        )


def _store_features(record: GeoFile, dataset: ParsedDataset) -> None:
    geometry_types: Counter[str] = Counter()
    crs_labels: set[str] = set()

    for index, parsed in enumerate(dataset.features):
        result = measure(parsed.geometry, parsed.crs)
        label = crs_label(parsed.crs) if parsed.crs is not None else None
        geometry_type = parsed.geometry.geom_type if parsed.geometry is not None else None

        geometry_types[geometry_type or "None"] += 1
        if label:
            crs_labels.add(label)

        record.features.append(
            Feature(
                index=index,
                layer=parsed.layer,
                geometry_type=geometry_type,
                geometry=mapping(parsed.geometry) if parsed.geometry is not None else None,
                crs=label,
                properties=parsed.properties,
                measurement_status=result.status,
                area_m2=result.area_m2,
                perimeter_m=result.perimeter_m,
                length_m=result.length_m,
                measurement_crs=result.crs,
                measurement_note=parsed.error or result.note,
            )
        )

    warnings = list(dataset.warnings)
    if len(crs_labels) > 1:
        warnings.append(f"Layers use different CRSs: {', '.join(sorted(crs_labels))}.")

    record.feature_count = len(dataset.features)
    record.geometry_type_counts = dict(geometry_types)
    record.crs = crs_labels.pop() if len(crs_labels) == 1 else ("MIXED" if crs_labels else None)
    record.warnings = warnings
