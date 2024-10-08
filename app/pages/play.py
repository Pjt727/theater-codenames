from fasthtml.common import *
from sqlalchemy import func, desc
from models.game import *
from models.errors import *
from sqlalchemy.orm import joinedload
from starlette.requests import Request
from make_app import app, PARTIALS_PREFIX
from multipart.exceptions import MultipartParseError
from pages.components import MessageKind, MessageStack, Page, Message


# there might be a better way to do this
def get_hx_value(query_selector: str, hx_val_key: str):
    return f'JSON.parse(qs("{query_selector}").getAttribute("hx-vals"))["{hx_val_key}"]'


def CardBoard(card: GameCard, game: Game, is_spy_master: bool = False):
    dyn_spy_master = get_hx_value("#spyMasterToggle", "is_spy_master")
    active_attributes = {
        "hx_post": app.url_path_for("guess", game_code=game.code),
        "hx_swap": "outerHTML",
        "hx_trigger": "click",
        "hx_vals": f'js:{{"game_card_id": {card.rowid}, "is_spy_master": {dyn_spy_master}}}',
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


def GamePolling(game: Game):
    return Div(
        id="game_polling",
        hx_trigger="every 500ms",
        hx_swap_oob="true",
        hx_get=app.url_path_for("update_game", game_code=game.code),
        hx_vals=f'js:{{"last_game_card_id": {game.last_game_card_id if game.last_game_card_id else -1}, "is_spy_master": JSON.parse(qs("#spyMasterToggle").getAttribute("hx-vals"))["is_spy_master"]}}',
        hidden=True,
    )


def GameBoard(game: Game, is_spy_master: bool = False, is_update: bool = False):
    game_cards = game.cards
    return Table(cls="table", id="gameBoard", hx_swap_oob="true" if is_update else None)(
        Tbody(
            GamePolling(game),
            *[
                Tr(
                    *[
                        CardBoard(game_card, game, is_spy_master)
                        for game_card in game_cards[i : i + CARDS_PER_ROW]
                    ]
                )
                for i in range(0, CARDS_PER_GAME, CARDS_PER_ROW)
            ],
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


def ConfirmButton(game_code: str):
    dyn_spy_master = get_hx_value("#spyMasterToggle", "is_spy_master")
    dyn_game_card = get_hx_value("#spyMasterToggle", "game_card_id")
    return Button(
        "Confirm Button",
        cls="btn btn-primary",
        id="spyMasterToggle",
        hx_swap="outerHTML",
        hx_swap_oob="true",
        hx_target="#gameBoard",
        hx_vals=f'js:{{"is_spy_master": {dyn_spy_master}, "game_card_id": {dyn_game_card}}}',
        hx_post=app.url_path_for("toggle_spymaster", game_code=game_code),
    )


@app.get("/play")
def play(request: Request):
    tags = session.scalars(select(Tag)).all()
    return Page(
        request,
        "Play",
        Form(cls="pt-5", hx_post=app.url_path_for("play"), hx_swap="none")(
            # surely there is a better way... for some reason I get parse errors
            #   on empty messages
            # seems to be an open issue: https://github.com/Kludex/python-multipart/issues/38
            Input(name="dummy_value", value="1", hidden=True),
            Div(cls="d-flex")(
                Button(
                    "Make Game",
                    type="submit",
                    cls="btn btn-primary me-3",
                ),
                *[
                    (
                        Input(
                            name=f"tag-{tag.rowid}", cls="form-check-input me-2", type="checkbox"
                        ),
                        Label(tag.name, cls="me-3"),
                    )
                    for tag in tags
                ],
            ),
        ),
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
async def make_game(request: Request, session_id: int | None = None, game_code: str | None = None):
    try:
        form_data = await request.form()
    except MultipartParseError:
        # since it is parsed early this error on an empty form would also happen early
        # I am not sure if there is a better way to check if form() will work
        return Message(Div(f"Please select some categories for the game"), kind=MessageKind.ERROR)

    # Extract the tags... I wonder if there is a better way to send this information using
    #   forms... Maybe it would just be better to us more client side js?
    # A bit a js maybe in the hx-vals of the form could check which are enabled bc
    #   doing it this way seems a bit hacky
    tag_prefix = "tag-"
    tag_ids: list[int] = []
    for key in form_data.keys():
        if not key.startswith(tag_prefix):
            continue
        try:
            tag_ids.append(int(key[len(tag_prefix) :]))
        except ValueError:
            # should never happen unless someone messes with requests
            return Message(Div("Malformed tag item"), kind=MessageKind.ERROR)

    if session_id is None:
        game_session = Session()
        session.add(game_session)
        # need to generate that id
        session.flush()
        groupers = [
            SessionTagGrouper(session_id=game_session.id, tag_id=tag_id) for tag_id in tag_ids
        ]
        session.add_all(groupers)
        session.flush()
    else:
        # Current idea is to require one of the past game codes be sent with
        #    this request
        # Since I do not want to make people log in this seems like a relatively
        #    secure option because as now people may just hit this url with a any session_id
        # If this is not the most recent game of the session then instead of making it would
        #    make sense to just redirect to the newest game
        assert game_code is not None
        game = session.scalar(
            select(Game)
            .options(joinedload(Game.session))
            .filter(Game.session_id == session_id)
            .filter(Game.code == game_code)
        )
        if game is None:
            return Message(Div("The game session no longer exists"), kind=MessageKind.ERROR)
        most_recent_game_code = session.scalar(
            select(Game.code)
            .filter(Game.session_id == session_id)
            .order_by(desc(Game.rowid))
            .limit(1)
        )
        assert most_recent_game_code is not None

        if most_recent_game_code != game.code:
            return HttpHeader(
                "HX-Redirect", app.url_path_for("play_game", game_code=most_recent_game_code)
            )

        game_session = game.session

    try:
        game = game_session.create_game()
    except NotEnoughCards as err:
        session.rollback()
        if session_id is None:
            # this was the first session that was being made which means
            #   the tags cards was not enough to fill a single game
            return Message(
                Div(
                    f"You need {err.needed_cards} cards to play a game but those tags only add up to {err.cards_left} cards."
                ),
                kind=MessageKind.ERROR,
            )
        # the other case that they finsihed the session by playing
        #   pretty much all the cards
        return Message(
            Div(
                f"There's only {err.cards_left} cards left to play within this session and you need {err.needed_cards} to play a game!"
            ),
            kind=MessageKind.ERROR,
        )

    session.commit()
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
        Button(
            "New Game",
            cls="btn btn-success",
            hx_post=app.url_path_for("make_game"),
            hx_swap="none",
            hx_vals={"session_id": game.session_id, "game_code": game.code},
        ),
        ConfirmButton(game.code),
        MessageStack(),
    )


# everything is an oob swap to make it easier to maybe do web connections later for
#   updating the game state
@app.get("/update/{game_code:str}")
def update_game(request: Request, is_spy_master: bool, last_game_card_id: int | None = None):
    game_code = request.path_params["game_code"]
    game = session.scalar(
        select(Game).filter(Game.code == game_code).options(joinedload(Game.cards))
    )
    if game is None:
        return HttpHeader("HX-Redirect", app.url_path_for("play"))

    # only update the game if the last game card guess is the same
    # there is a slight problem with this in that game may still become inconsistant
    #   but I think it is good enough
    if game.last_game_card_id == last_game_card_id:
        return

    red_guessed = len([c for c in game.cards if c.kind == GameCardKind.RED and c.is_guessed])
    red = len([c for c in game.cards if c.kind == GameCardKind.RED])
    blue_guessed = len([c for c in game.cards if c.kind == GameCardKind.BLUE and c.is_guessed])
    blue = len([c for c in game.cards if c.kind == GameCardKind.BLUE])
    black_guessed = len([c for c in game.cards if c.kind == GameCardKind.BLACK and c.is_guessed])
    black = len([c for c in game.cards if c.kind == GameCardKind.BLACK])
    tan_guessed = len([c for c in game.cards if c.kind == GameCardKind.TAN and c.is_guessed])
    tan = len([c for c in game.cards if c.kind == GameCardKind.TAN])
    return (
        GameBoard(game, is_spy_master=is_spy_master, is_update=True),
        Span(id=repr(GameCardKind.RED), hx_swap_oob="true")(f"{red_guessed}/{red}"),
        Span(id=repr(GameCardKind.BLUE), hx_swap_oob="true")(f"{blue_guessed}/{blue}"),
        Span(id=repr(GameCardKind.BLACK), hx_swap_oob="true")(f"{black_guessed}/{black}"),
        Span(id=repr(GameCardKind.TAN), hx_swap_oob="true")(f"{tan_guessed}/{tan}"),
    )


@app.post(f"{PARTIALS_PREFIX}/guess_card/{{game_code:str}}")
def guess(request: Request, game_card_id: int, is_spy_master: bool):
    game_card = GameCard.get(game_card_id)
    game = game_card.game
    assert not game_card.is_guessed
    game_code = request.path_params["game_code"]
    assert game.code == game_code
    game_card.is_guessed = True
    game.last_game_card_id = game_card_id
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
        CardBoard(game_card, game, is_spy_master),
        Span(id=repr(game_card.kind), hx_swap_oob="true")(f"{guess_card_count}/{same_card_count}"),
        GamePolling(game),
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


@app.post(f"{PARTIALS_PREFIX}/select_card/{{game_code:str}}")
def select_card(request: Request, is_spy_master: bool):
    game_code = request.path_params["game_code"]
    game = session.scalar(
        select(Game).filter(Game.code == game_code).options(joinedload(Game.cards))
    )
    assert game is not None
    return GameBoard(game, is_spy_master), ConfirmButton(game.code, is_spy_master)
