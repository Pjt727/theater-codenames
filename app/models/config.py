import os
from typing import Type, TypeVar, Tuple
from sqlalchemy import create_engine, Integer, select, Select, text, inspect as sa_inspect
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy.orm import DeclarativeBase

T = TypeVar("T")


class Base(DeclarativeBase):
    rowid: Mapped[int] = mapped_column(Integer, system=True)

    @classmethod
    def get(cls: Type[T], rowid: int) -> T:
        """Get the element with the sqlite rowid"""
        result = session.scalar(select(cls).filter(cls.rowid == rowid))  # pyright: ignore
        assert result is not None
        return result

    @classmethod
    def select(cls: Type[T], rowid: int) -> Select[Tuple[T]]:
        """The select of getting a row with the sqlite rowid"""
        return select(cls).filter(cls.rowid == rowid)  # pyright: ignore


# Database configuration
# Absolute paths need sqlite:////path (four slashes); relative stays sqlite:///file.db
DB_PATH = os.environ.get("DB_PATH", "cards.db")
DB_URI = f"sqlite:///{DB_PATH}"
# Create the engine and session
engine = create_engine(DB_URI)
session = Session(engine)


def run_migrations() -> None:
    """Add new columns to existing tables without dropping data."""
    with engine.connect() as conn:
        inspector = sa_inspect(engine)
        existing_tables = inspector.get_table_names()

        if "Sessions" in existing_tables:
            sessions_cols = {c["name"] for c in inspector.get_columns("Sessions")}
            if "has_warden" not in sessions_cols:
                conn.execute(text("ALTER TABLE Sessions ADD COLUMN has_warden BOOLEAN NOT NULL DEFAULT 0"))
            if "warden_token" not in sessions_cols:
                conn.execute(text("ALTER TABLE Sessions ADD COLUMN warden_token VARCHAR"))

        if "Games" in existing_tables:
            games_cols = {c["name"] for c in inspector.get_columns("Games")}
            if "active_team" not in games_cols:
                conn.execute(text("ALTER TABLE Games ADD COLUMN active_team VARCHAR"))
            if "winner" not in games_cols:
                conn.execute(text("ALTER TABLE Games ADD COLUMN winner VARCHAR"))

        conn.commit()
