from fasthtml.common import *
from sqlalchemy import func
from models.game import *
from sqlalchemy.orm import joinedload
from starlette.requests import Request
from make_app import app, PARTIALS_PREFIX
from pages.components import Page


def CardBoard(card: GameCard, is_spy_master: bool = False):
    hx_attributes = {
        "hx_post": app.url_path_for("guess"),
        "hx_swap": "outerHTML",
        "hx_trigger": "click",
        "hx_vals": {"game_card_id": card.rowid},
        "style": "cursor: pointer",
    }
    return Td(
        cls=f"border {card.kind.to_bs_class() if card.is_guessed or is_spy_master else ""} p-3",
        **({} if card.is_guessed else hx_attributes),
    )(card.card_phrase)


def GameBoard(game: Game):
    game_cards = game.cards
    return Table(cls="table")(
        Tbody(
            *[
                Tr(*[CardBoard(game_card) for game_card in game_cards[i : i + CARDS_PER_ROW]])
                for i in range(0, CARDS_PER_GAME, CARDS_PER_ROW)
            ]
        ),
    )


@app.get("/play")
def play(request: Request):
    return Page(request, "Play", Button("Make Random Game", hx_post=app.url_path_for("play")))


@app.post("/play")
def make_game():
    game = Game.create()
    return HttpHeader("HX-Redirect", app.url_path_for("play_game", game_code=game.code))


@app.get("/play/{game_code:str}")
def play_game(request: Request):
    game_code = request.path_params["game_code"]
    print(game_code)
    game = session.scalar(
        select(Game).filter(Game.code == game_code).options(joinedload(Game.cards))
    )
    if game is None:
        return HttpHeader("HX-Redirect", app.url_path_for("play"))
    print("holl")
    red = len([c for c in game.cards if c.kind == GameCardKind.RED])
    blue = len([c for c in game.cards if c.kind == GameCardKind.BLUE])
    black = len([c for c in game.cards if c.kind == GameCardKind.BLACK])
    tan = len([c for c in game.cards if c.kind == GameCardKind.TAN])
    return Page(
        request,
        "Play",
        GameBoard(game),
        Div(
            Span(cls="pe-3")(
                "Red:",
                Span(id=repr(GameCardKind.RED))(f"0/{red}"),
            ),
            Span(cls="pe-3")(
                "Blue:",
                Span(id=repr(GameCardKind.BLUE))(f"0/{blue}"),
            ),
            Span(cls="pe-3")(
                "Black:",
                Span(id=repr(GameCardKind.BLACK))(f"0/{black}"),
            ),
            Span(cls="pe-3")(
                "Tan:",
                Span(id=repr(GameCardKind.TAN))(f"0/{tan}"),
            ),
        ),
    )


@app.post("/guess_card")
def guess(game_card_id: int):
    game_card = GameCard.get(game_card_id)
    assert not game_card.is_guessed
    game_card.is_guessed = True
    session.commit()
    same_card_count = session.scalar(
        select(func.count())
        .select_from(GameCard)
        .filter(GameCard.kind == game_card.kind)
        # idk why game equilanve on the objects did not work
        .filter(GameCard.game_code == game_card.game_code)
    )
    guess_card_count = session.scalar(
        select(func.count())
        .select_from(GameCard)
        .filter(GameCard.kind == game_card.kind)
        .filter(GameCard.game_code == game_card.game_code)
        .filter(GameCard.is_guessed == True)
    )
    return (
        CardBoard(game_card),
        Span(id=repr(game_card.kind), hx_swap_oob="true")(f"{guess_card_count}/{same_card_count}"),
    )


@app.post("/toggle_spymaster")
def toggle_spymaster(game_card_id: int):
    return ()
