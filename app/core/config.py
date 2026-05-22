from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str

    # Redis
    REDIS_URL: str = "redis://localhost:6379"

    # JWT
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # Hugging Face
    HUGGINGFACE_API_KEY: str = ""

    # App
    APP_ENV: str = "development"

    class Config:
        env_file = ".env"


settings = Settings()