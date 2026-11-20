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


def _version(engine) -> str:
    with engine.connect() as connection:
        return connection.execute(text("SELECT version_num FROM alembic_version")).scalar()


def _indices(engine, table: str) -> set[str]:
    return {i["name"] for i in inspect(engine).get_indexes(table)}


def test_head_no_longer_has_the_companion_table(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'head.db'}")
    run_migrations(engine)
    assert _version(engine) == "0006"
    assert "companion_memories" not in inspect(engine).get_table_names()


def test_companion_drop_round_trips_upgrade_downgrade_upgrade(tmp_path):
    """0006 drops the retired companion table; its downgrade recreates exactly
    what 0005 created (same columns and index), and upgrading again drops it."""
    engine = build_engine(f"sqlite:///{tmp_path / 'round.db'}")
    config = _alembic_config()
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0005")
        connection.commit()
    created_by_0005 = (
        {c["name"] for c in inspect(engine).get_columns("companion_memories")},
        _indices(engine, "companion_memories"),
    )

    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.execute(
            text(
                "INSERT INTO conversations (id, customer_id, tenant_id, channel, status, "
                "created_at, updated_at) VALUES ('c1', 'k1', 'default', 'api', 'active', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.commit()
    assert "companion_memories" not in inspect(engine).get_table_names()

    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0005")
        connection.commit()
    assert _version(engine) == "0005"
    recreated = (
        {c["name"] for c in inspect(engine).get_columns("companion_memories")},
        _indices(engine, "companion_memories"),
    )
    assert recreated == created_by_0005

    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.commit()
    assert _version(engine) == "0006"
    assert "companion_memories" not in inspect(engine).get_table_names()
    with engine.connect() as connection:  # support data survives the round trip
        assert connection.execute(text("SELECT count(*) FROM conversations")).scalar() == 1


def test_history_naming_a_retired_agent_still_reads(tmp_path):
    """A database that ran the companion mode keeps messages whose agent_name is
    'companion': after upgrading, the conversation reads with agent_name None
    instead of failing on an unknown enum value."""
    from callforge.infrastructure.database import build_session_factory
    from callforge.infrastructure.repositories import SqlConversationRepository

    engine = build_engine(f"sqlite:///{tmp_path / 'legacy_agent.db'}")
    config = _alembic_config()
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0005")
        connection.execute(
            text(
                "INSERT INTO messages (id, conversation_id, role, content, agent_name, "
                "confidence, created_at) VALUES ('m1', 'c1', 'assistant', 'hola', "
                "'companion', 1.0, CURRENT_TIMESTAMP)"
            )
        )
        connection.commit()
    run_migrations(engine)

    session = build_session_factory(engine)()
    try:
        (message,) = SqlConversationRepository(session).list_messages("c1")
    finally:
        session.close()
    assert message.content == "hola"
    assert message.agent_name is None
