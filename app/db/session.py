from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from app.db.models import Base


def make_engine(database_url: str) -> Engine:
    return create_engine(database_url, pool_pre_ping=True, future=True)


def init_db(engine: Engine) -> None:
    """Create all tables. Use Alembic for schema evolution once the schema stabilises."""
    Base.metadata.create_all(engine)
