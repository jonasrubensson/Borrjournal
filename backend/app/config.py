from functools import lru_cache
from urllib.parse import quote_plus

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Databas. Sätt antingen POSTGRES_* var för sig, eller DATABASE_URL rakt av.
    # Delarna är att föredra: lösenordet kodas då korrekt, även om det innehåller
    # tecken som /, + eller @, vilket annars förstör anslutningssträngen.
    database_url_override: str = Field(
        default="",
        validation_alias=AliasChoices("DATABASE_URL", "DATABASE_URL_OVERRIDE"),
    )
    postgres_host: str = ""
    postgres_port: int = 5432
    postgres_db: str = "borrjournal"
    postgres_user: str = "borrjournal"
    postgres_password: str = ""
    sqlite_path: str = "./borrjournal.db"

    # Autentisering
    secret_key: str = "byt-ut-mig-i-produktion"
    token_expire_hours: int = 12
    algorithm: str = "HS256"

    # Filer
    data_dir: str = "./data"
    # Var backuperna hamnar. Peka mot en monterad nätverksdisk eller extern volym
    # så att de inte ligger på samma disk som det de skyddar. Tom = data_dir/backups.
    backup_dir: str = ""
    max_upload_mb: int = 25

    # Första administratören skapas vid start om databasen är tom
    bootstrap_admin: str = "admin"
    bootstrap_password: str = "byt-mig-direkt"

    # Sätt till true för att fylla databasen med demodata vid tom start
    seed_demo: bool = False

    # Adressuppslag. Töm geocoder_url för att stänga av funktionen helt.
    geocoder_url: str = "https://nominatim.openstreetmap.org/search"
    geocoder_country_code: str = "se"
    geocoder_country_name: str = "Sverige"
    # Nominatims villkor kräver en User-Agent som identifierar den som frågar.
    # Sätt GEOCODER_USER_AGENT i .env till något med en kontaktadress i, annars
    # riskerar uppslagen att avvisas.
    geocoder_user_agent: str = "Borrjournal/2.3 (kundregister for vattenborrning)"

    # Säkerhetsheaders. Stäng av bara vid felsökning.
    security_headers: bool = True
    # Sätt true när en tjänst som Cloudflare ligger framför och behöver köra
    # sina egna skript och rutor för robotkontroll.
    allow_challenge_scripts: bool = False
    # Extra källor att tillåta i policyn, mellanslagsseparerat. Till exempel
    # "https://challenges.cloudflare.com https://static.cloudflareinsights.com"
    csp_extra_sources: str = ""

    # SGU:s brunnsarkiv, öppna data. Töm sgu_bulk_url för att stänga av.
    # Bulkfiler per län, en fil per anrop. Verifierat format.
    sgu_bulk_url: str = "https://resource.sgu.se/data/oppnadata/grundvatten/brunnar"
    sgu_base_url: str = "https://resource.sgu.se/oppnadata/grundvatten/brunnar/v1"

    class Config:
        env_file = ".env"
        env_prefix = ""
        populate_by_name = True

    @property
    def database_url(self) -> str:
        if self.database_url_override:
            return self.database_url_override
        if self.postgres_host:
            user = quote_plus(self.postgres_user)
            password = quote_plus(self.postgres_password)
            return (
                f"postgresql+asyncpg://{user}:{password}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            )
        return f"sqlite+aiosqlite:///{self.sqlite_path}"


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
