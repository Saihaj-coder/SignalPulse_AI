"""Central configuration for SignalPulse AI.

All settings are loaded from environment variables (typically from a local
``.env`` file). This gives us one place to read configuration and keeps secrets
(API keys, DB passwords) out of the source code.

Usage
-----
    from signalpulse.config import settings

    print(settings.NEO4J_URI)
    print(settings.gemini_ready)

The module uses ``pydantic-settings``, which validates types and provides clear
error messages if a value is malformed.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root = the folder that contains this package (one level up from here).
BASE_DIR: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = BASE_DIR / "data"
RAW_DIR: Path = DATA_DIR / "raw"
PROCESSED_DIR: Path = DATA_DIR / "processed"


class Settings(BaseSettings):
    """Strongly-typed application settings, loaded from ``.env``."""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",  # ignore unrelated env vars instead of erroring
        case_sensitive=False,
    )

    # --- Neo4j ---
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USERNAME: str = "neo4j"
    NEO4J_PASSWORD: str = "signalpulse123"
    NEO4J_DATABASE: str = "neo4j"

    # --- LLM providers ---
    GOOGLE_API_KEY: str | None = None
    GROQ_API_KEY: str | None = None
    MISTRAL_API_KEY: str | None = None
    DEEPSEEK_API_KEY: str | None = None
    # Primary provider; others with keys configured are tried as fallbacks (in order).
    LLM_PROVIDER: str = "groq"
    GEMINI_MODEL: str = "gemini-3.5-flash"
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    MISTRAL_MODEL: str = "mistral-small-latest"
    DEEPSEEK_MODEL: str = "deepseek-chat"
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"

    # --- Embeddings (local, free) ---
    EMBEDDING_MODEL: str = "BAAI/bge-small-en-v1.5"
    EMBEDDING_DIM: int = 384

    # --- Data-source API keys ---
    NVD_API_KEY: str | None = None
    REGULATIONS_GOV_API_KEY: str = "DEMO_KEY"

    # --- Pipeline tuning ---
    CHUNK_SIZE: int = Field(default=800, ge=100)
    CHUNK_OVERLAP: int = Field(default=100, ge=0)
    VECTOR_TOP_K: int = Field(default=6, ge=1)
    SIMILARITY_THRESHOLD: float = Field(default=0.60, ge=0.0, le=1.0)

    # ----- Convenience properties -----
    @property
    def gemini_ready(self) -> bool:
        """True if a Google Gemini key is configured."""
        return bool(self.GOOGLE_API_KEY)

    @property
    def groq_ready(self) -> bool:
        """True if a Groq key is configured."""
        return bool(self.GROQ_API_KEY)

    @property
    def mistral_ready(self) -> bool:
        """True if a Mistral key is configured."""
        return bool(self.MISTRAL_API_KEY)

    @property
    def deepseek_ready(self) -> bool:
        """True if a DeepSeek key is configured."""
        return bool(self.DEEPSEEK_API_KEY)

    @property
    def nvd_ready(self) -> bool:
        """True if an NVD API key is configured (optional but recommended)."""
        return bool(self.NVD_API_KEY)

    def ensure_dirs(self) -> None:
        """Create the local data directories if they do not exist yet."""
        for directory in (DATA_DIR, RAW_DIR, PROCESSED_DIR):
            directory.mkdir(parents=True, exist_ok=True)


# A single shared instance imported across the project.
settings = Settings()
settings.ensure_dirs()
