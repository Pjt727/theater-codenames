from datetime import datetime
from sqlalchemy import (
    ForeignKey,
    Integer,
    String,
    Boolean,
    Enum,
    DateTime,
    UniqueConstraint,
    ForeignKeyConstraint,
)
import random
import string
from sqlalchemy.sql.expression import func
from fasthtml.ft import *
from models.config import Base, session
from models.errors import *
from sqlalchemy import select
from make_app import TOKEN_SIZE
from sqlalchemy.orm import Mapped, validates
from sqlalchemy.orm import mapped_column, relationship
from enum import Enum as PyEnum
from typing import Optional

CARDS_PER_GAME = 25
GUESS_AMOUNT = 8
BLACK_AMOUNT = 1
# the game logic that must follow if you mess with the default card amounts
assert CARDS_PER_GAME > (GUESS_AMOUNT * 2 + 1) + BLACK_AMOUNT
CARDS_PER_ROW = 5
GAME_CODE_SIZE = 6


class Card(Base):
    __tablename__ = "Cards"
    phrase: Mapped[str] = mapped_column(String(), primary_key=True)

    game_cards: Mapped[list["GameCard"]] = relationship(back_populates="card")
    tag_card_groupers: Mapped[list["TagCardGrouper"]] = relationship(back_populates="card")


class Tag(Base):
    __tablename__ = "Tags"
    id: Mapped[int] = mapped_column(Integer(), primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(), unique=True)

    tag_card_groupers: Mapped[list["TagCardGrouper"]] = relationship(back_populates="tag")
    session_tag_groupers: Mapped[list["SessionTagGrouper"]] = relationship(back_populates="tag")


# allows the many to many relationship between tag and card
class TagCardGrouper(Base):
    __tablename__ = "TagCardGroupers"
    tag_id: Mapped[str] = mapped_column(String(), ForeignKey("Tags.id"), primary_key=True)
    card_phrase: Mapped[str] = mapped_column(String(), ForeignKey("Cards.phrase"), primary_key=True)

    tag: Mapped["Tag"] = relationship(back_populates="tag_card_groupers")
    card: Mapped["Card"] = relationship(back_populates="tag_card_groupers")


# A series of games where there are diffent cards in each game analogous to always
#   using different cards when you sit down to play a series of game
class Session(Base):
    __tablename__ = "Sessions"
    # internally sqlite will have rowid and this autoincrementing id be the same
    id: Mapped[int] = mapped_column(Integer(), primary_key=True, autoincrement=True)
    name: Mapped[Optional[str]] = mapped_column(String())
    date_created: Mapped[datetime] = mapped_column(DateTime(), default=datetime.now)

    session_tag_groupers: Mapped[list["SessionTagGrouper"]] = relationship(back_populates="session")
    games: Mapped[list["Game"]] = relationship(back_populates="session")

    def create_game(self) -> "Game":
        # figure out who goes first and gets the additional
        #     card
        if random.random() < 0.5:
            red = [GameCardKind.RED] * (GUESS_AMOUNT + 1)
            blue = [GameCardKind.BLUE] * GUESS_AMOUNT
        else:
            red = [GameCardKind.RED] * GUESS_AMOUNT
            blue = [GameCardKind.BLUE] * (GUESS_AMOUNT + 1)

        # TODO ensure random string is not already in the db
        random_string = "".join(random.choices(string.ascii_uppercase, k=GAME_CODE_SIZE))
        game = Game(code=random_string, session_id=self.id)
        session.add(game)

        # get the random cards for the next game
        previous_phrases_in_session = (
            select(GameCard.card_phrase).join(Game).filter(Game.session_id == self.id)
        )
        tags = (
            select(Tag.id).join(SessionTagGrouper).filter(SessionTagGrouper.session_id == self.id)
        )
        random_cards = session.scalars(
            select(Card)
            .join(TagCardGrouper)
            .filter(~Card.phrase.in_(previous_phrases_in_session))
            .filter(TagCardGrouper.tag_id.in_(tags))
            .order_by(func.random())
            .limit(CARDS_PER_GAME)
        ).all()

        if len(random_cards) != CARDS_PER_GAME:
            session.rollback()
            raise NotEnoughCards(
                "Need more cards", needed_cards=CARDS_PER_GAME, cards_left=len(random_cards)
            )

        # create all game cards
        black = [GameCardKind.BLACK] * BLACK_AMOUNT
        tan = [GameCardKind.TAN] * (CARDS_PER_GAME - (GUESS_AMOUNT * 2 + 1) - BLACK_AMOUNT)
        kinds = red + blue + black + tan
        random.shuffle(kinds)
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


class Game(Base):
    __tablename__ = "Games"
    code: Mapped[str] = mapped_column(String(), primary_key=True)
    session_id: Mapped[int] = mapped_column(Integer(), ForeignKey("Sessions.id"))
    # integrity does not really matter as much for this because it should only be used
    #   to match game state
    last_updated: Mapped[datetime] = mapped_column(DateTime(), default=datetime.now)

    cards: Mapped[list["GameCard"]] = relationship(back_populates="game")
    session: Mapped["Session"] = relationship(back_populates="games")


# many to many relationship between session and the tags it contains
class SessionTagGrouper(Base):
    __tablename__ = "SessionTagGroupers"
    session_id: Mapped[int] = mapped_column(Integer(), ForeignKey("Sessions.id"), primary_key=True)
    tag_id: Mapped[str] = mapped_column(String(), ForeignKey("Tags.id"), primary_key=True)

    tag: Mapped["Tag"] = relationship(back_populates="session_tag_groupers")
    session: Mapped["Session"] = relationship(back_populates="session_tag_groupers")


class GameCardKind(PyEnum):
    RED = "Red"
    BLUE = "Blue"
    TAN = "Tan"
    BLACK = "Black"

    def to_bs_class(self) -> str:
        if self == GameCardKind.RED:
            return "bg-danger"
        elif self == GameCardKind.BLUE:
            return "bg-primary"
        elif self == GameCardKind.BLACK:
            return "text-light bg-black"
        elif self == GameCardKind.TAN:
            return "bg-warning"
        raise ValueError("Unexpected enum")

    def to_styles(self) -> str:
        if self == GameCardKind.RED:
            return "background-color: #dc3546;"
        elif self == GameCardKind.BLUE:
            return "background-color: #0d6efd;"
        elif self == GameCardKind.BLACK:
            return "background-color: #343a40; color: #fff;"
        elif self == GameCardKind.TAN:
            return "background-color: #fec007;"
        raise ValueError("Unexpected enum")

    def __repr__(self) -> str:
        # id's don't work well with .'s
        return f"game_card_kind_{self.name}"


class GameCard(Base):
    __tablename__ = "GameCards"

    is_guessed = mapped_column(Boolean())
    card_phrase: Mapped[str] = mapped_column(String(), ForeignKey("Cards.phrase"), primary_key=True)
    game_code: Mapped[str] = mapped_column(String(), ForeignKey("Games.code"), primary_key=True)
    kind: Mapped[GameCardKind] = mapped_column(Enum(GameCardKind))
    # the index goes from top left to bottom right starting at 0
    index: Mapped[int] = mapped_column(Integer())

    card: Mapped["Card"] = relationship(back_populates="game_cards")
    game: Mapped["Game"] = relationship(back_populates="cards")
    selections: Mapped[list["Selection"]] = relationship(back_populates="game_card")
    __table_args__ = (UniqueConstraint("index", "game_code", name="uq_index_game_code"),)

    @validates("index")
    def validate_index(self, _, index):
        if index >= CARDS_PER_GAME or index < 0:
            raise ValueError(f"Invalid index of {index} must be in range")
        return index

    # this is wrong but it is consistantly wrong so idrc
    def to_row_col(self) -> tuple[int, int]:
        row, col = divmod(self.index, CARDS_PER_ROW)
        return (row if row != 0 else CARDS_PER_ROW, col if col != 0 else CARDS_PER_ROW)


class Selection(Base):
    __tablename__ = "Selections"
    token: Mapped[str] = mapped_column(String(), primary_key=True)
    game_code: Mapped[str] = mapped_column(String(), primary_key=True)
    card_phrase: Mapped[Optional[str]] = mapped_column(String())
    game_card: Mapped["GameCard"] = relationship(back_populates="selections")

    __table_args__ = (
        ForeignKeyConstraint(
            ["game_code", "card_phrase"], ["GameCards.game_code", "GameCards.card_phrase"]
        ),
    )

    # this ensures that people can't mess with the tokens too bad
    #    like make them super big
    @validates("token")
    def validate_token(self, _, token):
        if len(token) > 64:
            raise ValueError("Invalid token size")
        return token
