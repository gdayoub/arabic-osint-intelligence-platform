"""Application settings loaded from environment variables.

This module centralizes runtime configuration so ingestion, processing,
and dashboard components stay decoupled from deployment details.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List

from dotenv import load_dotenv

# Load local .env when running outside Docker.
load_dotenv()


@dataclass(slots=True)
class Settings:
    """Runtime settings for the Arabic OSINT platform."""

    app_name: str = "Arabic OSINT Platform"
    environment: str = "development"
    log_level: str = "INFO"

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "arabic_osint"
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    database_url_override: str | None = None

    request_timeout_seconds: int = 15
    scrape_delay_seconds: float = 0.8
    max_articles_per_source: int = 120
    max_pages_per_source: int = 6
    scrape_sections_enabled: bool = True
    scrape_archives_enabled: bool = True
    min_article_body_chars: int = 120

    remove_stopwords_default: bool = True
    topic_keywords_path: str = "src/config/topic_keywords.json"

    aljazeera_seed_urls: List[str] | None = None
    bbc_seed_urls: List[str] | None = None
    cnn_seed_urls: List[str] | None = None

    @property
    def database_url(self) -> str:
        """Build SQLAlchemy connection URL for PostgreSQL."""
        if self.database_url_override:
            return self.database_url_override
        return (
            "postgresql+psycopg2://"
            f"{self.postgres_user}:{self.postgres_password}@"
            f"{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @classmethod
    def from_env(cls) -> "Settings":
        """Create settings from environment variables with sane defaults."""
        return cls(
            app_name=os.getenv("APP_NAME", "Arabic OSINT Platform"),
            environment=os.getenv("ENVIRONMENT", "development"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            postgres_host=os.getenv("POSTGRES_HOST", "localhost"),
            postgres_port=int(os.getenv("POSTGRES_PORT", "5432")),
            postgres_db=os.getenv("POSTGRES_DB", "arabic_osint"),
            postgres_user=os.getenv("POSTGRES_USER", "postgres"),
            postgres_password=os.getenv("POSTGRES_PASSWORD", "postgres"),
            database_url_override=os.getenv("DATABASE_URL"),
            request_timeout_seconds=int(os.getenv("REQUEST_TIMEOUT_SECONDS", "15")),
            scrape_delay_seconds=float(os.getenv("SCRAPE_DELAY_SECONDS", "0.8")),
            max_articles_per_source=int(os.getenv("MAX_ARTICLES_PER_SOURCE", "120")),
            max_pages_per_source=int(os.getenv("MAX_PAGES_PER_SOURCE", "6")),
            scrape_sections_enabled=_env_bool(
                os.getenv("SCRAPE_SECTIONS_ENABLED", "true")
            ),
            scrape_archives_enabled=_env_bool(
                os.getenv("SCRAPE_ARCHIVES_ENABLED", "true")
            ),
            min_article_body_chars=int(os.getenv("MIN_ARTICLE_BODY_CHARS", "120")),
            remove_stopwords_default=os.getenv("REMOVE_STOPWORDS_DEFAULT", "true").lower()
            == "true",
            topic_keywords_path=os.getenv(
                "TOPIC_KEYWORDS_PATH", "src/config/topic_keywords.json"
            ),
            aljazeera_seed_urls=_split_csv(
                os.getenv(
                    "ALJAZEERA_SEED_URLS",
                    (
                        "https://www.aljazeera.net/news,"
                        "https://www.aljazeera.net/politics,"
                        "https://www.aljazeera.net/economy"
                    ),
                )
            ),
            bbc_seed_urls=_split_csv(
                os.getenv(
                    "BBC_ARABIC_SEED_URLS",
                    (
                        "https://www.bbc.com/arabic,"
                        "https://www.bbc.com/arabic/middleeast,"
                        "https://www.bbc.com/arabic/business,"
                        "https://www.bbc.com/arabic/world"
                    ),
                )
            ),
            cnn_seed_urls=_split_csv(
                os.getenv(
                    "CNN_ARABIC_SEED_URLS",
                    (
                        "https://arabic.cnn.com/middle-east,"
                        "https://arabic.cnn.com/world,"
                        "https://arabic.cnn.com/business"
                    ),
                )
            ),
        )


def _split_csv(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _env_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


SETTINGS = Settings.from_env()
