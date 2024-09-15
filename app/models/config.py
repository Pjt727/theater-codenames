from typing import Type, TypeVar, Tuple
from sqlalchemy import create_engine, Integer, select, Select
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
DB_URI = "sqlite:///cards.db"
# Create the engine and session
engine = create_engine(DB_URI)
session = Session(engine)
