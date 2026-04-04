from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_VERSION: str = "0.1.0"
    DATABASE_URL: str = "postgresql://user:password@localhost:5432/medcoolteste"
    SECRET_KEY: str = "change-me-in-production"
    DEBUG: bool = False

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
