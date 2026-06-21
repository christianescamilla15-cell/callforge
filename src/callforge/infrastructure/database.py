from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# First Alembic revision; pre-Alembic databases get stamped here before upgrading.
BASELINE_REVISION = "0001"


class Base(DeclarativeBase):
    pass


def build_engine(database_url: str) -> Engine:
    connect_args = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(database_url, connect_args=connect_args)


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def create_all(engine: Engine) -> None:
    """Direct schema creation, bypassing Alembic. Unit-test helper only."""
    from callforge.infrastructure import models  # noqa: F401  (register tables)

    Base.metadata.create_all(engine)


def run_migrations(engine: Engine) -> None:
    """Bring the database to the latest Alembic revision.

    Databases created before Alembic was introduced (tables exist, no
    alembic_version) are stamped at the baseline revision first, so the
    baseline create_table migration is skipped and only newer migrations run.
    """
    from alembic import command
    from alembic.config import Config

    import callforge.migrations as migrations_pkg

    config = Config()
    config.set_main_option(
        "script_location", str(Path(migrations_pkg.__file__).resolve().parent)
    )
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        inspector = inspect(connection)
        tables = inspector.get_table_names()
        if "alembic_version" not in tables and "conversations" in tables:
            command.stamp(config, BASELINE_REVISION)
        command.upgrade(config, "head")
        connection.commit()
