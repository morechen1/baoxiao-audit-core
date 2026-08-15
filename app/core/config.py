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
    semantic_parser_enabled: bool = False
    semantic_screening_enabled: bool = False
    semantic_parser_max_chunk_chars: int = Field(default=4000, ge=500, le=12000)
    semantic_parser_chunk_overlap: int = Field(default=160, ge=0, le=1000)
    max_semantic_chunks_per_document: int = Field(default=12, ge=1, le=32)
    max_provider_calls_per_screening: int = Field(default=16, ge=1, le=64)
    semantic_parser_max_text_length: int = Field(default=50_000, ge=1000, le=100_000)
    semantic_parser_cache_enabled: bool = True
    semantic_parser_cache_entries: int = Field(default=256, ge=1, le=4096)
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)
    max_batch_items: int = Field(default=20, ge=1, le=100)
    embedding_enabled: bool = False
    embedding_provider: str = "disabled"
    embedding_model: str = ""
    ocr_enabled: bool = False
    ocr_provider: str = "disabled"


@lru_cache
def get_settings() -> Settings:
    return Settings()
