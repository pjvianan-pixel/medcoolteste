from pydantic import model_validator
from pydantic_settings import BaseSettings

_DEFAULT_SECRET_KEY = "change-me-in-production"


class Settings(BaseSettings):
    APP_VERSION: str = "0.1.0"
    DATABASE_URL: str = "postgresql+asyncpg://user:password@localhost:5432/medcoolteste"
    SECRET_KEY: str = _DEFAULT_SECRET_KEY
    DEBUG: bool = False
    JWT_EXPIRES_MINUTES: int = 60
    PRESENCE_TIMEOUT_SECONDS: int = 30
    MATCH_OFFER_BATCH_SIZE: int = 5
    PLATFORM_FEE_PERCENT: int = 20

    # Pagar.me payment gateway settings
    PAGARME_API_KEY: str = ""
    PAGARME_BASE_URL: str = "https://api.pagar.me/core/v5"
    PAGARME_WEBHOOK_SECRET: str = ""
    PAGARME_PLATFORM_RECIPIENT_ID: str = ""

    # Medical document storage
    DOCUMENTS_STORAGE_PATH: str = "documents"
    DOCUMENTS_BASE_URL: str = "/static/documents"

    # Cancellation and no-show policy
    CANCELLATION_MIN_HOURS_FULL_REFUND: int = 24
    CANCELLATION_LATE_FEE_PERCENT: int = 50
    CANCELLATION_NO_SHOW_REFUND_PERCENT: int = 0
    CANCELLATION_NO_SHOW_GRACE_MINUTES: int = 15

    model_config = {"env_file": ".env", "extra": "ignore"}

    @model_validator(mode="after")
    def _validate_secret_key(self) -> "Settings":
        if not self.DEBUG and self.SECRET_KEY == _DEFAULT_SECRET_KEY:
            raise ValueError(
                "SECRET_KEY must be changed from the default value in production. "
                "Set DEBUG=true or provide a strong SECRET_KEY via the environment."
            )
        return self


settings = Settings()
