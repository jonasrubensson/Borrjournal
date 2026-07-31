from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import settings

engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):
        """WAL gör att en skrivning inte låser ute läsarna, vilket spelar roll när två
        montörer använder appen samtidigt. Utan detta blir det 'database is locked'."""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


def _sql_type(column) -> str:
    try:
        return column.type.compile(engine.dialect)
    except Exception:  # noqa: BLE001
        return "TEXT"


def _default_clause(column) -> str:
    """Enkla standardvärden så att befintliga rader får något vettigt."""
    default = getattr(column, "default", None)
    if default is not None and getattr(default, "is_scalar", False):
        value = default.arg
        if isinstance(value, bool):
            return f" DEFAULT {1 if value else 0}"
        if isinstance(value, (int, float)):
            return f" DEFAULT {value}"
        if isinstance(value, str):
            escaped = value.replace("'", "''")
            return f" DEFAULT '{escaped}'"
    return ""


async def _add_missing_columns(conn) -> None:
    """Lägger till kolumner som tillkommit sedan databasen skapades.

    Bara tillägg, aldrig ändringar eller borttag. Det räcker för hur appen
    utvecklas och gör att en uppgradering inte kräver att man tömmer databasen.
    Byggs schemat om på riktigt behövs Alembic.
    """
    from sqlalchemy import inspect, text

    def existing(sync_conn):
        inspector = inspect(sync_conn)
        tables = set(inspector.get_table_names())
        return {t: {c["name"] for c in inspector.get_columns(t)} for t in tables}

    present = await conn.run_sync(existing)

    for table in Base.metadata.sorted_tables:
        if table.name not in present:
            continue
        for column in table.columns:
            if column.name in present[table.name]:
                continue
            if not column.nullable and column.default is None and not column.primary_key:
                print(f"[borrjournal] hoppar över {table.name}.{column.name}: saknar standardvärde")
                continue
            ddl = (
                f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" '
                f"{_sql_type(column)}{_default_clause(column)}"
            )
            await conn.execute(text(ddl))
            print(f"[borrjournal] la till kolumn {table.name}.{column.name}")


async def init_db() -> None:
    from . import models  # noqa: F401  säkerställer att modellerna är registrerade

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _add_missing_columns(conn)
