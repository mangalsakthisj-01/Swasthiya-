from logging.config import fileConfig
from alembic import context
from sqlalchemy import engine_from_config, pool, text
from backend.database import Base, database_url

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
config.set_main_option("sqlalchemy.url", database_url().replace("%", "%%"))
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=database_url(), target_metadata=target_metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"}, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}),
                                     prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        if connection.dialect.name == "postgresql":
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
            connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
        if connection.dialect.name == "postgresql":
            with connection.begin():
                connection.execute(text("ALTER TABLE complaints ADD COLUMN IF NOT EXISTS geom geometry(Point,4326)"))
                connection.execute(text("ALTER TABLE hotspots ADD COLUMN IF NOT EXISTS geom geometry(Geometry,4326)"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS ix_complaints_geom_gist ON complaints USING GIST (geom)"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS ix_hotspots_geom_gist ON hotspots USING GIST (geom)"))

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
