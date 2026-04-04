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
