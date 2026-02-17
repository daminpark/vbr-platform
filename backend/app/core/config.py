"""Application configuration."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Host Tools API
    hosttools_auth_token: str = ""

    # Google Gemini API
    gemini_api_key: str = ""

    # Database (SQLite for local dev, PostgreSQL for production)
    database_url: str = "sqlite+aiosqlite:///./vbr.db"

    # Security
    secret_key: str = "change-me-in-production"
    owner_pin: str = "1234"
    cleaner_pin: str = "1234"  # Same as master code

    # ntfy.sh notifications (self-hosted)
    ntfy_url: str = ""  # e.g. https://ntfy.yourdomain.com
    ntfy_topic: str = "vbr"  # notification topic name
    ntfy_token: str = ""  # access token if auth enabled

    # Home Assistant (Rental Manager) via Tailscale
    ha_195_url: str = ""  # Set in .env
    ha_193_url: str = ""  # Set in .env

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = True
    base_url: str = "http://localhost:8000"  # Public URL for webhooks

    # AI Settings
    ai_auto_reply: bool = False  # Start in draft-only mode
    ai_confidence_threshold: float = 0.95  # High bar initially

    # Polling fallback interval (seconds)
    hosttools_poll_interval: int = 120  # 2 minutes

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
