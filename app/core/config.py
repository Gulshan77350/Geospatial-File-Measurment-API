"""Application settings, loaded from environment variables (prefix ``GEO_``) or a ``.env`` file."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GEO_", env_file=".env", extra="ignore")

    app_name: str = "Geospatial File Measurement API"
    database_url: str = "sqlite:///./data/app.db"
    storage_dir: Path = Path("data/uploads")

    # Upload / archive safety limits.
    max_upload_bytes: int = 50 * 1024 * 1024
    max_uncompressed_bytes: int = 500 * 1024 * 1024
    max_zip_members: int = 1_000

    # Pagination defaults for list endpoints.
    default_page_size: int = 100
    max_page_size: int = 1_000


@lru_cache
def get_settings() -> Settings:
    return Settings()
