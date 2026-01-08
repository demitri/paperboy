from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    INDEX_DB_PATH: str
    TAR_DIR_PATH: str

    class Config:
        env_file = ".env"