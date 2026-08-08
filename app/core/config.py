from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = "sqlite:///./baoxiao.db"
    data_dir: Path = Path("data")
    user_agent: str = "BaoxiaoAuditCore/0.1 (+compliance-research)"
    http_timeout_seconds: float = 20
    max_download_bytes: int = 50 * 1024 * 1024
    llm_enabled: bool = False
    llm_provider: Literal["deterministic_fixture", "openai_compatible"] = "deterministic_fixture"
    llm_base_url: str = ""
    llm_api_key: str = Field(default="", repr=False)
    llm_model: str = ""
    llm_timeout_seconds: float = Field(default=30, gt=0, le=120)
    semantic_screening_enabled: bool = False
    embedding_enabled: bool = False
    embedding_provider: str = "disabled"
    embedding_model: str = ""
    ocr_enabled: bool = False
    ocr_provider: str = "disabled"


@lru_cache
def get_settings() -> Settings:
    return Settings()
