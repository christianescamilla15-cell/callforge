from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

import callforge.migrations as migrations_pkg
from callforge.infrastructure.database import (
    BASELINE_REVISION,
    build_engine,
    run_migrations,
)


def _alembic_config() -> Config:
    config = Config()
    config.set_main_option(
        "script_location", str(Path(migrations_pkg.__file__).resolve().parent)
    )
    return config


def test_fresh_database_migrates_to_head(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    run_migrations(engine)
    inspector = inspect(engine)
    assert "alembic_version" in inspector.get_table_names()
    assert "conversations" in inspector.get_table_names()
    columns = {c["name"] for c in inspector.get_columns("knowledge_documents")}
    assert "embedding" in columns  # migration 0002 applied


def test_pre_alembic_database_is_stamped_then_upgraded(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'legacy.db'}")

    # Faithful simulation of the deployed pre-Alembic database: exactly the
    # frozen baseline schema, with no alembic_version table.
    config = _alembic_config()
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, BASELINE_REVISION)
        connection.execute(text("DROP TABLE alembic_version"))
        connection.commit()

    run_migrations(engine)  # must stamp baseline, then apply 0002+

    inspector = inspect(engine)
    columns = {c["name"] for c in inspector.get_columns("knowledge_documents")}
    assert "embedding" in columns
    with engine.connect() as connection:
        version = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar()
    assert version is not None and version != BASELINE_REVISION  # moved past baseline


def test_run_migrations_is_idempotent(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'twice.db'}")
    run_migrations(engine)
    run_migrations(engine)  # second run is a no-op, must not raise
    assert "conversations" in inspect(engine).get_table_names()
