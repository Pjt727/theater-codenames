from fasthtml.common import *
import asyncio
from sqlalchemy import func, desc, exists
from models.game import *
from models.errors import *
from sqlalchemy.orm import joinedload
from starlette.requests import Request
from make_app import app, PARTIALS_PREFIX, SITE_TOKEN
from multipart.exceptions import MultipartParseError
from pages.components import MessageKind, MessageStack, Page, Message
from starlette.websockets import WebSocket, WebSocketDisconnect
from datetime import datetime
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from enum import Enum
from starlette.applications import Starlette
from typing import Callable
import uuid


SELECTION_TEXT = "\u261d"
MAX_SELECTION_COUNT = 3


# these game roles are currently not at all enforces since you can choose whichever you'd like
# the only things this changes is ui you can access, endpoints know nothing about this
# would probably be annoying to try to enforce anything
# this is in place so people playing honestly with friends don't get tempted to or accidently hit
#     the wrong buttons to mess up the game
class GameRole(Enum):
    SPYMASTER = "spymaster"
    """can make guesses, can signal selection (only on non-spymaster board), 
    see all colors before reveal, and can make new game"""
    OPERATIVE = "operative"
    """can make guesses, signal selection, and can make new game"""
    VIEWER = "viewer"
    """can signal selection and can continue to next game"""

    def __repr__(self) -> str:
        # id's don't work well with .'s
        return f"game_role_{self.value}"


# using a grid so that selections can be placed in the correct
#   col / row when cells are being replaced
board_css = f"""
.board {{
    display: grid;
    grid-template-columns: repeat({CARDS_PER_ROW}, 1fr);
    grid-template-rows: repeat({math.ceil(CARDS_PER_GAME / CARDS_PER_ROW)}, 1fr);
    gap: 10px;
}}
"""


def CardBoard(
    card: GameCard,
    game: Game,
    is_update: bool = True,
    is_users_selection: bool = False,
):
    active_attributes = {
        "id": f"game-card-{card.rowid}",
        "hx_post": app.url_path_for("select_card", game_code=game.code),
        "hx_swap": "none",
        "hx_swap_oob": "true" if is_update else None,
        "hx_trigger": "click",
        "hx_vals": {"game_card_id": card.rowid},
    }

    row, col = divmod(card.index, CARDS_PER_ROW)
    card_class = card.kind.to_bs_class() if card.is_guessed else ""
    return Div(
        # unselected-card matches the generated css for spy masters to have color
        cls=f"z-1 border text-center unselected-card-{card.index} {card_class} p-3 {"text-decoration-underline" if is_users_selection else ""}",
        style=f"grid-area: {row} / {col} / {row} / {col}; {"" if card.is_guessed else "cursor: pointer"}",
        **({} if card.is_guessed else active_attributes),
    )(
        Div(cls=f"{"text-decoration-line-through" if card.is_guessed else ""}")(
            card.card_phrase.title(),
            Span(
                "🙊" if card.kind == GameCardKind.BLACK else "🐵",
                cls=f"z-3 text-bg-light position-absolute top-0 start-100 translate-middle badge rounded-pill z-3",
            )
            if card.is_guessed
            else None,
        )
    )


def GameBoard(game: Game, is_update: bool = True):
    return Div(cls="board", id="gameBoard", hx_swap_oob="true" if is_update else None)(
        *[CardBoard(game_card, game, is_update) for game_card in game.cards],
        None if is_update else Selections(game),
    )


def ConfirmButton(game_code: str, game_card_id: int | None = None, is_update: bool = True):
    return Button(
        "Confirm Selection",
        cls="btn btn-primary",
        id="confirm-card",
        hx_swap="none",
        hx_swap_oob="true" if is_update else None,
        hx_vals={"game_card_id": game_card_id},
        hx_post=app.url_path_for("guess", game_code=game_code),
        hx_disable=None if game_card_id is None else "true",
        disabled="" if game_card_id is None else None,
    )


def NextGameButton(game: Game, text: str, enabled: bool = True, is_update: bool = True):
    return Button(
        text,
        id="next_game",
        cls="btn btn-success",
        hx_post=app.url_path_for("make_game"),
        hx_swap="none",
        hx_swap_oob="true" if is_update else None,
        hx_vals={"session_id": game.session_id, "game_code": game.code},
        hx_disable=None if enabled else "true",
        disabled=None if enabled else "",
    )


def UserSelectedStyle(game_card: GameCard | None, is_update: bool = True):
    style = Style(id="userSelectedStyle", hx_swap_oob="true" if is_update else None)
    if game_card is None:
        return style
    return style(f"#game-card-{game_card.rowid} {{ text-decoration: underline; }}")


def Selections(game: Game, is_update: bool = True):
    selection_containers = []
    for card in game.cards:
        if card.is_guessed:
            continue
        selection_count = len(card.selections)
        if selection_count == 0:
            continue
        if selection_count > MAX_SELECTION_COUNT:
            selection_pill_text = f"{SELECTION_TEXT} X {selection_count}"
        else:
            selection_pill_text = SELECTION_TEXT * selection_count
        row, col = divmod(card.index, CARDS_PER_ROW)
        selection_containers.append(
            Div(
                cls="z-0 position-relative",
                style=f"grid-area: {row} / {col} / {row} / {col}",
            )(
                Span(
                    selection_pill_text,
                    cls=f"z-3 text-bg-light border position-absolute top-0 start-0 translate-middle badge rounded-pill z-3",
                )
            )
        )
    return Div(
        id="selections", style="display: contents", hx_swap_oob="true" if is_update else None
    )(*selection_containers)


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
                        oninput="this.value = this.value.toUpperCase()",
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
        # update this to make sure to push the updated new game button
        game.last_updated = datetime.now()
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
    game = session.scalar(select(Game).filter(Game.code == game_code.upper()))
    if game is None:
        return Message(Div(f"The game `{game_code}` could not be found"), kind=MessageKind.ERROR)

    return HttpHeader("HX-Redirect", app.url_path_for("play_game", game_code=game.code))


@app.get("/play/{game_code:str}")
def play_game(request: Request, role: str | None = None):
    game_code = request.path_params["game_code"]
    game = session.scalar(
        select(Game)
        .filter(Game.code == game_code)
        .options(joinedload(Game.cards).joinedload(GameCard.selections))
    )
    if game is None:
        return HttpHeader("HX-Redirect", app.url_path_for("play"))
    # have them choose a role so they don't accidently hit wrong buttons
    if role is None:
        return Page(
            request,
            "Play (Picking Role)",
            Form(
                cls="container",
                hx_get=app.url_path_for("play_game", game_code=game_code),
            )(
                Select(id="role", name="role", cls="form-select mb-2")(
                    Option(GameRole.SPYMASTER.value.title(), value=repr(GameRole.SPYMASTER)),
                    Option(GameRole.OPERATIVE.value.title(), value=repr(GameRole.OPERATIVE)),
                    Option(GameRole.VIEWER.value.title(), value=repr(GameRole.VIEWER)),
                ),
                Button("Select Role", cls="btn btn-primary", type="input"),
            ),
        )
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
        # there might be a better way to apply these styles for the spymasters
        Style(board_css),
        Style(
            "\n".join(
                [
                    f".unselected-card-{card.index} {{ {card.kind.to_styles()}; }}"
                    for card in game.cards
                ]
            )
        )
        if role == repr(GameRole.SPYMASTER)
        else None,
        UserSelectedStyle(None, is_update=False),
        Div(hx_ext="ws", ws_connect=app.url_path_for("play_connect", game_code=game_code)),
        GameBoard(game, is_update=False),
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
        NextGameButton(game, "New Game", is_update=False)
        if (role == repr(GameRole.SPYMASTER)) or (role == repr(GameRole.OPERATIVE))
        else NextGameButton(game, "Continue", False, is_update=False),
        ConfirmButton(game.code, is_update=False)
        if (role == repr(GameRole.SPYMASTER)) or (role == repr(GameRole.OPERATIVE))
        else None,
        MessageStack(),
    )


# everything is an oob swap to make it easier to maybe do web connections later for
#   updating the game state
async def updated_game(game_code: str, last_updated: str | None):
    last_updated_date = datetime.fromisoformat(last_updated) if last_updated else None
    # could maybe do a smaller query since a lot requests are expected to not change
    game = session.scalar(
        select(Game)
        .filter(Game.code == game_code)
        .options(joinedload(Game.cards).joinedload(GameCard.selections))
    )
    if game is None:
        return HttpHeader("HX-Redirect", app.url_path_for("play"))

    # only update if out of sync
    if game.last_updated == last_updated_date:
        print("last updated", game.last_updated, last_updated_date)
        print(game.rowid, game.code)
        return

    red_guessed = len([c for c in game.cards if c.kind == GameCardKind.RED and c.is_guessed])
    red = len([c for c in game.cards if c.kind == GameCardKind.RED])
    blue_guessed = len([c for c in game.cards if c.kind == GameCardKind.BLUE and c.is_guessed])
    blue = len([c for c in game.cards if c.kind == GameCardKind.BLUE])
    black_guessed = len([c for c in game.cards if c.kind == GameCardKind.BLACK and c.is_guessed])
    black = len([c for c in game.cards if c.kind == GameCardKind.BLACK])
    tan_guessed = len([c for c in game.cards if c.kind == GameCardKind.TAN and c.is_guessed])
    tan = len([c for c in game.cards if c.kind == GameCardKind.TAN])
    more_recent_game = session.scalar(
        select(Game)
        .filter(Game.code != game.code)
        .filter(Game.session_id == game.session_id)
        .order_by(desc(Game.rowid))
        .limit(1)
    )
    return (
        *[CardBoard(c, game) for c in game.cards if c.is_guessed],
        Span(id=repr(GameCardKind.RED), hx_swap_oob="true")(f"{red_guessed}/{red}"),
        Span(id=repr(GameCardKind.BLUE), hx_swap_oob="true")(f"{blue_guessed}/{blue}"),
        Span(id=repr(GameCardKind.BLACK), hx_swap_oob="true")(f"{black_guessed}/{black}"),
        Span(id=repr(GameCardKind.TAN), hx_swap_oob="true")(f"{tan_guessed}/{tan}"),
        None if more_recent_game is None else NextGameButton(more_recent_game, "Next Game", True),
        Selections(game),
    )


@dataclass
class WebSocketPlayerData:
    websocket: WebSocket
    game_code: str
    last_updated: str | None = None  # isoformatted


players: dict[str, WebSocketPlayerData] = {}


async def update_game():
    for uuid, player in players.items():
        try:
            game = session.scalar(select(Game).where(Game.code == player.game_code))
            if game is None:
                del players[uuid]
                continue
            fhtml_game = await updated_game(player.game_code, player.last_updated)
            if fhtml_game is None:
                # case where game shouldn't be updated
                continue
            await player.websocket.send_text(to_xml(fhtml_game))
            player.last_updated = str(game.last_updated)
        except Exception as err:
            print(err)
            del players[uuid]


class PlayConnect(WebSocketEndpoint):
    encoding = "http"

    async def on_connect(self, websocket: WebSocket):
        await websocket.accept()
        game_code = websocket.path_params["game_code"]
        self.uuid = str(uuid.uuid4())
        players[self.uuid] = WebSocketPlayerData(websocket=websocket, game_code=game_code)

    async def on_disconnect(self, websocket: WebSocket, close_code: int):
        del players[self.uuid]


app.add_websocket_route("/play-connect/{game_code:str}", PlayConnect, name="play_connect")


@app.post(f"{PARTIALS_PREFIX}/guess_card/{{game_code:str}}")
async def guess(request: Request, game_card_id: int):
    game_card = GameCard.get(game_card_id)
    game = game_card.game
    assert not game_card.is_guessed
    game_code = request.path_params["game_code"]
    assert game.code == game_code
    game_card.is_guessed = True
    game.last_updated = datetime.now()
    session.commit()
    await update_game()


@app.post(f"{PARTIALS_PREFIX}/select_card/{{game_code:str}}")
async def select_card(request: Request, game_card_id: int):
    game_code = request.path_params["game_code"]
    # i think there's a better sqlalchemy api for this query
    game = session.scalar(select(Game).filter(Game.code == game_code))
    game_exists = game is not None
    assert game_exists
    token = request.session.get(SITE_TOKEN)
    assert token is not None
    card = GameCard.get(game_card_id)
    assert card is not None
    game.last_updated = datetime.now()
    session.commit()
    current_selection = session.scalar(
        select(Selection).filter(Selection.token == token and Selection.game_code == game_code)
    )
    if current_selection is not None and current_selection.card_phrase == card.card_phrase:
        # they reselected the same card so unselect it
        session.delete(current_selection)
        session.commit()
        await update_game()
        return UserSelectedStyle(None), ConfirmButton(game_code, None)
    new_selection = {
        "token": token,
        "game_code": game_code,
        "card_phrase": card.card_phrase,
    }
    update_selection = (
        sqlite_insert(Selection)
        .values([new_selection])
        .on_conflict_do_update(
            set_={
                Selection.card_phrase: card.card_phrase,
                Selection.game_code: game_code,
            }
        )
    )
    session.execute(update_selection)
    session.commit()
    await update_game()
    return UserSelectedStyle(card), ConfirmButton(game_code, game_card_id)


async def connect_player(ws: WebSocket, send):
    game_code = ws.path_params["game_code"]


async def disconnect_player(send):
    pass
