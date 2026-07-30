from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Databas. Postgres i drift, sqlite fungerar för lokal utveckling.
    database_url: str = "sqlite+aiosqlite:///./borrjournal.db"

    # Autentisering
    secret_key: str = "byt-ut-mig-i-produktion"
    token_expire_hours: int = 12
    algorithm: str = "HS256"

    # Filer
    data_dir: str = "./data"
    max_upload_mb: int = 25

    # Första administratören skapas vid start om databasen är tom
    bootstrap_admin: str = "admin"
    bootstrap_password: str = "byt-mig-direkt"

    # Sätt till true för att fylla databasen med demodata vid tom start
    seed_demo: bool = False

    class Config:
        env_file = ".env"
        env_prefix = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

ALLOWED_TYPES = {
    "application/pdf": ("dokument", ".pdf"),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ("dokument", ".docx"),
    "application/msword": ("dokument", ".doc"),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ("dokument", ".xlsx"),
    "text/plain": ("dokument", ".txt"),
    "image/jpeg": ("bild", ".jpg"),
    "image/png": ("bild", ".png"),
    "image/webp": ("bild", ".webp"),
    "image/heic": ("bild", ".heic"),
}
