"""ORM models: an uploaded file and the features extracted from it (with their measurements)."""

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC datetimes, also on SQLite (which stores datetimes without an offset)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class FileStatus(str, enum.Enum):
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class FileType(str, enum.Enum):
    SHAPEFILE = "SHAPEFILE"
    KML = "KML"


class MeasurementStatus(str, enum.Enum):
    MEASURED = "MEASURED"
    NOT_APPLICABLE = "NOT_APPLICABLE"  # e.g. points: nothing to measure
    UNSUPPORTED = "UNSUPPORTED"  # geometry type we do not measure (e.g. GeometryCollection)
    FAILED = "FAILED"  # missing/invalid geometry, unknown CRS, transformation failure


class GeoFile(Base):
    __tablename__ = "files"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    filename: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[FileType] = mapped_column(Enum(FileType, native_enum=False))
    size_bytes: Mapped[int] = mapped_column(Integer)
    storage_path: Mapped[str] = mapped_column(String(1024))
    status: Mapped[FileStatus] = mapped_column(Enum(FileStatus, native_enum=False), default=FileStatus.PROCESSING)
    crs: Mapped[str | None] = mapped_column(String(255), default=None)
    feature_count: Mapped[int] = mapped_column(Integer, default=0)
    geometry_type_counts: Mapped[dict] = mapped_column(JSON, default=dict)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)

    features: Mapped[list["Feature"]] = relationship(
        back_populates="file",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Feature.index",
    )


class Feature(Base):
    __tablename__ = "features"
    __table_args__ = (UniqueConstraint("file_id", "index", name="uq_feature_file_index"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), index=True)
    index: Mapped[int] = mapped_column(Integer)
    layer: Mapped[str | None] = mapped_column(String(255), default=None)
    geometry_type: Mapped[str | None] = mapped_column(String(64), default=None)
    geometry: Mapped[dict | None] = mapped_column(JSON, default=None)  # GeoJSON, in the source CRS
    crs: Mapped[str | None] = mapped_column(String(255), default=None)
    properties: Mapped[dict] = mapped_column(JSON, default=dict)

    measurement_status: Mapped[MeasurementStatus] = mapped_column(Enum(MeasurementStatus, native_enum=False))
    area_m2: Mapped[float | None] = mapped_column(Float, default=None)
    perimeter_m: Mapped[float | None] = mapped_column(Float, default=None)
    length_m: Mapped[float | None] = mapped_column(Float, default=None)
    measurement_crs: Mapped[str | None] = mapped_column(String(512), default=None)
    measurement_note: Mapped[str | None] = mapped_column(Text, default=None)

    file: Mapped[GeoFile] = relationship(back_populates="features")
