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
CARDS_PER_ROW = 5


class Card(Base):
    __tablename__ = "Cards"
    phrase: Mapped[str] = mapped_column(String(), primary_key=True)

    game_cards: Mapped[list["GameCard"]] = relationship(back_populates="card")


class GameCardState(PyEnum):
    UNGUESSED = "Unguessed"
    RED = "Red"
    BLUE = "Blue"
    BLACK = "Black"
    TAN = "Tan"

    def to_bs_class(self) -> str:
        if self == GameCardState.UNGUESSED:
            return ""
        elif self == GameCardState.RED:
            return "--bs-danger-border-subtle"
        elif self == GameCardState.BLUE:
            return "--bs-info-border-subtle"
        elif self == GameCardState.BLACK:
            return "--bs-dark-border-subtle"
        elif self == GameCardState.TAN:
            return "--bs-warning-border-subtle"
        raise ValueError("Unexpected enum")


class Game(Base):
    __tablename__ = "Games"
    code: Mapped[str] = mapped_column(String(), primary_key=True)

    cards: Mapped["GameCard"] = relationship(back_populates="game")

    def __ft__(self):
        game_cards = session.scalars(
            select(GameCard).filter(GameCard.game == self).order_by(asc(GameCard.state))
        )
        card_elements = []
        for i, game_card in enumerate(game_cards, start=1):
            card_elements.append(game_card)
            if i % 5 == 0:
                card_elements.append(Br())
        print(card_elements)
        return Div(*card_elements)

    @staticmethod
    def create() -> "Game":
        random_string = "".join(random.choices(string.digits, k=6))
        # TODO ensure random string is not already in the db
        game = Game(code=random_string)

        random_cards = session.scalars(
            select(Card).order_by(func.random()).limit(CARDS_PER_GAME)
        ).all()
        assert len(random_cards) == CARDS_PER_GAME
        if random.random() < 0.5:
            red = [GameCardKind.RED] * (GUESS_AMOUNT + 1)
            blue = [GameCardKind.BLUE] * GUESS_AMOUNT
            tan = [GameCardKind.BLUE] * (CARDS_PER_GAME - (GUESS_AMOUNT * 2 + 1))
            kinds = red + blue + tan
        else:
            red = [GameCardKind.RED] * GUESS_AMOUNT
            blue = [GameCardKind.BLUE] * (GUESS_AMOUNT + 1)
            tan = [GameCardKind.BLUE] * (CARDS_PER_GAME - (GUESS_AMOUNT * 2 + 1))
            kinds = red + blue + tan

        assert len(random_cards) == CARDS_PER_GAME
        random.shuffle(kinds)

        # create all game cards
        game_cards = [
            GameCard(
                state=GameCardState.UNGUESSED,
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


class GameCard(Base):
    __tablename__ = "GameCards"

    state: Mapped[GameCardState] = mapped_column(Enum(GameCardState), primary_key=True)
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

    def __ft__(self):
        return Div(
            cls=f"{self.state.to_bs_class()} p-3 d-inline border",
            style="width: 100px; length: 200px;",
        )(self.card_phrase)
