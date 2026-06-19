from __future__ import with_statement

import os, json, sys, re

from client.errors import ConfigurationError

from alembic import context
from sqlalchemy import engine_from_config, pool
from logging.config import fileConfig

# Ensure that the root directory of the application is part of the path
ALEMBIC_ROOT = os.path.dirname(os.path.abspath(__file__))
CONTEXTDB_ROOT = os.path.dirname(ALEMBIC_ROOT)

if not ALEMBIC_ROOT in sys.path:
    sys.path.append(ALEMBIC_ROOT)
if not CONTEXTDB_ROOT in sys.path:
    sys.path.append(CONTEXTDB_ROOT)


# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
fileConfig(config.config_file_name)


# Ensure that a database URL was provided in the environment configuration
DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    raise ConfigurationError('Unable to initialize ContextDB, invalid database URL. Check '
        + 'DATABASE_URL environment variable.')

# Create SQLalchemy string: interpolate '%' to '%%' to provide interoperability with Alembic.
# Refer to https://stackoverflow.com/questions/39849641/in-flask-migrate-valueerror-invalid-interpolation-syntax-in-connection-string-a
config.set_main_option('sqlalchemy.url', DATABASE_URL.replace('%', '%%'))


# ContextDB Managed Models
from contextdb.db.base import DbBase

# for 'autogenerate' support
target_metadata = DbBase.metadata


def run_migrations_offline():
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url, target_metadata=target_metadata, literal_binds=True)

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix='sqlalchemy.',
        poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
