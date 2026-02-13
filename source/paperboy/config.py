from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    INDEX_DB_PATH: str
    TAR_DIR_PATH: str

    # Optional upstream server for fallback when papers not found locally
    UPSTREAM_SERVER_URL: Optional[str] = None
    UPSTREAM_TIMEOUT: float = 30.0
    UPSTREAM_ENABLED: bool = True

    # Cache configuration for offline paper retrieval
    CACHE_DIR_PATH: Optional[str] = None
    CACHE_MAX_SIZE_GB: float = 1.0

    # IR cache configuration
    IR_CACHE_DIR_PATH: Optional[str] = None
    IR_CACHE_MAX_SIZE_GB: float = 5.0

    # arXiv direct fallback (last resort when local and upstream both fail)
    ARXIV_FALLBACK_ENABLED: bool = True
    ARXIV_TIMEOUT: float = 30.0

    # USPTO patent configuration (optional — enables /patent/ endpoints)
    PATENT_INDEX_DB_PATH: Optional[str] = None
    PATENT_BULK_DIR_PATH: Optional[str] = None

    # Typesense search configuration
    TYPESENSE_HOST: str = "localhost"
    TYPESENSE_PORT: int = 8108
    TYPESENSE_PROTOCOL: str = "http"
    TYPESENSE_API_KEY: Optional[str] = None
    TYPESENSE_ENABLED: bool = False
    TYPESENSE_COLLECTION: str = "papers"

    class Config:
        env_file = ".env"