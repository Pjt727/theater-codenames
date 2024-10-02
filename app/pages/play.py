from fasthtml.common import *
from sqlalchemy import func
from models.game import *
from sqlalchemy.orm import joinedload
from starlette.requests import Request
from make_app import app, PARTIALS_PREFIX
from pages.components import MessageKind, MessageStack, Page, Message


def CardBoard(card: GameCard, is_spy_master: bool = False):
    active_attributes = {
        "hx_post": app.url_path_for("guess"),
        "hx_swap": "outerHTML",
        "hx_trigger": "click",
        # I think there might be a better way to do this
        # if not then I would probably like utility js function to extract an hx_val from an
        # html element
        "hx_vals": f'js:{{"game_card_id": {card.rowid}, "is_spy_master": JSON.parse(qs("#spyMasterToggle").getAttribute("hx-vals"))["is_spy_master"]}}',
        "hx_include": "#spyMasterToggle",
        "style": "cursor: pointer",
    }

    card_class = card.kind.to_bs_class() if card.is_guessed or is_spy_master else ""
    return Td(
        cls=f"border {card_class} p-3",
        **({} if card.is_guessed else active_attributes),
    )(
        Div(cls="position-relative")(
            card.card_phrase,
            Span(
                "🙊" if card.kind == GameCardKind.BLACK else "🐵",
                cls=f"bg-light-subtle position-absolute top-0 start-100 translate-middle badge rounded-pill z-3",
            )
            if is_spy_master and card.is_guessed
            else None,
        )
    )


def GameBoard(game: Game, is_spy_master: bool = False):
    game_cards = game.cards
    return Table(cls="table", id="gameBoard")(
        Tbody(
            *[
                Tr(
                    *[
                        CardBoard(game_card, is_spy_master)
                        for game_card in game_cards[i : i + CARDS_PER_ROW]
                    ]
                )
                for i in range(0, CARDS_PER_GAME, CARDS_PER_ROW)
            ]
        ),
    )


def SpyMasterButton(game_code: str, is_spy_master: bool):
    return Button(
        "Toggle Spy Master",
        cls="btn btn-primary",
        id="spyMasterToggle",
        hx_swap="outerHTML",
        hx_swap_oob="true",
        hx_target="#gameBoard",
        hx_vals={"is_spy_master": is_spy_master},
        hx_post=app.url_path_for("toggle_spymaster", game_code=game_code),
    )


@app.get("/play")
def play(request: Request):
    return Page(
        request,
        "Play",
        Button("Make Random Game", cls="btn btn-primary", hx_post=app.url_path_for("play")),
        Form(cls="pt-5", hx_post=app.url_path_for("find_game"), hx_swap="none")(
            Div(cls="d-flex")(
                Button("Find Game", cls="btn btn-primary me-2", type="submit"),
                Div(cls="input-group flex-grow1 w-25")(
                    Input(
                        cls="form-control",
                        id="game_code",
                        name="game_code",
                        placeholder="X" * GAME_CODE_SIZE,
                        inputmode="text",
                    ),
                    Span("Game Code", cls="input-group-text"),
                ),
            ),
        ),
        MessageStack(),
    )


@app.post("/play")
def make_game():
    game = Game.create()
    return HttpHeader("HX-Redirect", app.url_path_for("play_game", game_code=game.code))


# done as a separate route to play_game for error handling and later possible spymaster locking
@app.post(f"{PARTIALS_PREFIX}/find_game")
def find_game(game_code: str):
    game = session.scalar(select(Game).filter(Game.code == game_code))
    if game is None:
        return Message(Div(f"The game `{game_code}` could not be found"), kind=MessageKind.ERROR)

    return HttpHeader("HX-Redirect", app.url_path_for("play_game", game_code=game.code))


@app.get("/play/{game_code:str}")
def play_game(request: Request):
    game_code = request.path_params["game_code"]
    game = session.scalar(
        select(Game).filter(Game.code == game_code).options(joinedload(Game.cards))
    )
    if game is None:
        return HttpHeader("HX-Redirect", app.url_path_for("play"))
    red_guessed = len([c for c in game.cards if c.kind == GameCardKind.RED and c.is_guessed])
    red = len([c for c in game.cards if c.kind == GameCardKind.RED])
    blue_guessed = len([c for c in game.cards if c.kind == GameCardKind.BLUE and c.is_guessed])
    blue = len([c for c in game.cards if c.kind == GameCardKind.BLUE])
    black_guessed = len([c for c in game.cards if c.kind == GameCardKind.BLACK and c.is_guessed])
    black = len([c for c in game.cards if c.kind == GameCardKind.BLACK])
    tan_guessed = len([c for c in game.cards if c.kind == GameCardKind.TAN and c.is_guessed])
    tan = len([c for c in game.cards if c.kind == GameCardKind.TAN])
    return Page(
        request,
        "Play",
        GameBoard(game),
        Div(
            Span(cls="pe-3")(
                "Red:",
                Span(id=repr(GameCardKind.RED))(f"{red_guessed}/{red}"),
            ),
            Span(cls="pe-3")(
                "Blue:",
                Span(id=repr(GameCardKind.BLUE))(f"{blue_guessed}/{blue}"),
            ),
            Span(cls="pe-3")(
                "Black:",
                Span(id=repr(GameCardKind.BLACK))(f"{black_guessed}/{black}"),
            ),
            Span(cls="pe-3")(
                "Tan:",
                Span(id=repr(GameCardKind.TAN))(f"{tan_guessed}/{tan}"),
            ),
        ),
        SpyMasterButton(game.code, False),
    )


@app.post(f"{PARTIALS_PREFIX}/guess_card")
def guess(game_card_id: int, is_spy_master: bool):
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
        CardBoard(game_card, is_spy_master),
        Span(id=repr(game_card.kind), hx_swap_oob="true")(f"{guess_card_count}/{same_card_count}"),
    )


@app.post(f"{PARTIALS_PREFIX}/toggle_spymaster/{{game_code:str}}")
def toggle_spymaster(request: Request, is_spy_master: bool):
    is_spy_master = not is_spy_master
    game_code = request.path_params["game_code"]
    game = session.scalar(
        select(Game).filter(Game.code == game_code).options(joinedload(Game.cards))
    )
    assert game is not None
    return GameBoard(game, is_spy_master), SpyMasterButton(game.code, is_spy_master)
