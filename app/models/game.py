from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Integer,
    String,
    Boolean,
    Enum,
    ForeignKeyConstraint,
    DateTime,
    UniqueConstraint,
)
import random
import string
from sqlalchemy.sql.expression import func
from fasthtml.ft import *
from models.config import Base, session
from sqlalchemy import select, asc
from sqlalchemy.orm import Mapped, validates
from sqlalchemy.orm import mapped_column, relationship
from enum import Enum as PyEnum
from typing import Optional

CARDS_PER_GAME = 25
GUESS_AMOUNT = 8
BLACK_AMOUNT = 1
assert CARDS_PER_GAME > (GUESS_AMOUNT * 2 + 1) + BLACK_AMOUNT
CARDS_PER_ROW = 5
GAME_CODE_SIZE = 6


class Card(Base):
    __tablename__ = "Cards"
    phrase: Mapped[str] = mapped_column(String(), primary_key=True)

    game_cards: Mapped[list["GameCard"]] = relationship(back_populates="card")


class Game(Base):
    __tablename__ = "Games"
    code: Mapped[str] = mapped_column(String(), primary_key=True)
    cards: Mapped[list["GameCard"]] = relationship(back_populates="game")

    @staticmethod
    def create() -> "Game":
        # figure out who goes first and gets the additional
        #     card
        if random.random() < 0.5:
            red = [GameCardKind.RED] * (GUESS_AMOUNT + 1)
            blue = [GameCardKind.BLUE] * GUESS_AMOUNT
        else:
            red = [GameCardKind.RED] * GUESS_AMOUNT
            blue = [GameCardKind.BLUE] * (GUESS_AMOUNT + 1)
        black = [GameCardKind.BLACK] * BLACK_AMOUNT
        tan = [GameCardKind.TAN] * (CARDS_PER_GAME - (GUESS_AMOUNT * 2 + 1) - BLACK_AMOUNT)
        kinds = red + blue + black + tan
        random.shuffle(kinds)

        # TODO ensure random string is not already in the db
        random_string = "".join(random.choices(string.ascii_uppercase, k=GAME_CODE_SIZE))
        game = Game(code=random_string)
        session.add(game)

        random_cards = session.scalars(
            # Is there a sqlite way to seed the results
            #   might be useful for two people using this and the same word list
            #   would get the same words
            select(Card).order_by(func.random()).limit(CARDS_PER_GAME)
        ).all()
        assert len(random_cards) == CARDS_PER_GAME
        # create all game cards
        game_cards = [
            GameCard(
                is_guessed=False,
                card_phrase=card.phrase,
                game_code=game.code,
                index=i,
                kind=kind,
            )
            for i, (card, kind) in enumerate(zip(random_cards, kinds))
        ]
        session.add_all(game_cards)
        session.commit()
        return game


class GameCardKind(PyEnum):
    RED = "Red"
    BLUE = "Blue"
    TAN = "Tan"
    BLACK = "Black"

    def to_bs_class(self) -> str:
        if self == GameCardKind.RED:
            return "bg-danger-subtle"
        elif self == GameCardKind.BLUE:
            return "bg-primary-subtle"
        elif self == GameCardKind.BLACK:
            return "text-light bg-black"
        elif self == GameCardKind.TAN:
            return "bg-warning-subtle"
        raise ValueError("Unexpected enum")

    def __repr__(self) -> str:
        # id's don't work well with .'s
        return f"game_card_kind_{self.name}"


class GameCard(Base):
    __tablename__ = "GameCards"

    is_guessed = mapped_column(Boolean(), primary_key=True)
    card_phrase: Mapped[str] = mapped_column(String(), ForeignKey("Cards.phrase"), primary_key=True)
    game_code: Mapped[str] = mapped_column(String(), ForeignKey("Games.code"), primary_key=True)
    kind: Mapped[GameCardKind] = mapped_column(Enum(GameCardKind))
    # the index goes from top left to bottom right starting at 0
    index: Mapped[int] = mapped_column(Integer())

    card: Mapped["Card"] = relationship(back_populates="game_cards")

    game: Mapped["Game"] = relationship(back_populates="cards")
    __table_args__ = (UniqueConstraint("index", "game_code", name="uq_index_game_code"),)

    @validates("index")
    def validate_index(self, _, index):
        if index >= CARDS_PER_GAME or index < 0:
            raise ValueError(f"Invalid index of {index} must be in range")
        return index
