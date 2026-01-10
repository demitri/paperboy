from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    INDEX_DB_PATH: str
    TAR_DIR_PATH: str

    # Optional upstream server for fallback when papers not found locally
    UPSTREAM_SERVER_URL: Optional[str] = None
    UPSTREAM_TIMEOUT: float = 30.0
    UPSTREAM_ENABLED: bool = True

    class Config:
        env_file = ".env"