from fasthtml.common import *
import asyncio
from sqlalchemy import func, desc, exists
from models.game import *
from models.errors import *
from sqlalchemy.orm import joinedload
from starlette.requests import Request
from starlette.responses import RedirectResponse
from make_app import app, PARTIALS_PREFIX, SITE_TOKEN, IS_DARK_MODE_TOKEN, SITE_URL
from multipart.exceptions import MultipartParseError
from pages.components import MessageKind, MessageStack, Page, Message
from starlette.websockets import WebSocket, WebSocketDisconnect
from datetime import datetime
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from enum import Enum
from starlette.applications import Starlette
from typing import Callable
from dataclasses import dataclass, field
import uuid
import secrets


SELECTION_TEXT = "\u261d"
MAX_SELECTION_COUNT = 3


# These game roles are unenforced in non-warden games (players choose their own).
# In warden games, roles are enforced server-side via PlayerSessionRole.
class GameRole(Enum):
    SPYMASTER = "spymaster"
    """can signal selection, see all colors before reveal; in non-warden games can also confirm and make new game"""
    OPERATIVE = "operative"
    """can make guesses, signal selection, and can make new game (non-warden only)"""
    VIEWER = "viewer"
    """can signal selection only"""

    def __repr__(self) -> str:
        # id's don't work well with .'s
        return f"game_role_{self.value}"


def get_player_session_role(game_session_id: int, token: str) -> "PlayerSessionRole | None":
    return session.scalar(
        select(PlayerSessionRole)
        .filter(PlayerSessionRole.session_id == game_session_id)
        .filter(PlayerSessionRole.token == token)
    )


def get_visible_tokens(game: "Game", viewer_token: str) -> set[str]:
    """Return the set of tokens whose selections this viewer should see."""
    player_roles = session.scalars(
        select(PlayerSessionRole).filter(PlayerSessionRole.session_id == game.session_id)
    ).all()

    viewer_role = next((r for r in player_roles if r.token == viewer_token), None)
    if viewer_role is None:
        return set()

    active_team = game.active_team

    if viewer_role.role == "WARDEN":
        # Warden sees active team's non-spymasters
        return {r.token for r in player_roles if r.team == active_team and r.role != "SPYMASTER"}
    elif viewer_role.role == "VIEWER":
        # Viewers see their own team's selections
        return {r.token for r in player_roles if r.team == viewer_role.team}
    elif viewer_role.role == "SPYMASTER":
        # Spymasters see own team's spymasters + active team's all players
        return {
            r.token
            for r in player_roles
            if (r.team == viewer_role.team and r.role == "SPYMASTER") or r.team == active_team
        }

    return set()


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
    allow_selection: bool = True,
):
    active_attributes = {
        "hx_post": app.url_path_for("select_card", game_code=game.code),
        "hx_swap": "none",
        "hx_trigger": "click",
        "hx_vals": {"game_card_id": card.rowid},
    }

    row, col = card.to_row_col()
    card_class = card.kind.to_bs_class() if card.is_guessed else "bg-white"
    clickable = allow_selection and not card.is_guessed
    return Div(
        # unselected-card matches the generated css for spy masters to have color
        id=f"game-card-{card.rowid}",
        hx_swap_oob="true" if is_update else None,
        cls=f"rounded-3 position-relative border text-center unselected-card-{card.index} {card_class} p-3 {"text-decoration-underline" if is_users_selection else ""}",
        style=f"grid-area: {row} / {col} / {row} / {col}; {"opacity: 0.45; " if card.is_guessed else ""}{"" if not clickable else "cursor: pointer"}",
        **({} if not clickable else active_attributes),
    )(
        Div(cls=f"{"text-decoration-line-through" if card.is_guessed else ""}")(
            card.card_phrase.title(),
        )
    )


def GameBoard(
    game: Game,
    is_update: bool = True,
    allow_selection: bool = True,
    visible_tokens: "set[str] | None" = None,
):
    return Div(cls="board", id="gameBoard", hx_swap_oob="true" if is_update else None)(
        *[CardBoard(game_card, game, is_update, allow_selection=allow_selection) for game_card in game.cards],
        None if is_update else Selections(game, is_update, visible_tokens=visible_tokens),
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
        hx_disable="true" if game_card_id is None else None,
        disabled="" if game_card_id is None else None,
    )


def NextGameButton(game: Game, enabled: bool = True, is_update: bool = True):
    more_recent_game = session.scalar(
        select(Game)
        .filter(Game.code != game.code)
        .filter(Game.session_id == game.session_id)
        .filter(Game.rowid > game.rowid)
        .limit(1)
    )
    # no need to return anything if there is not a new game
    if not more_recent_game and is_update:
        return None
    if more_recent_game:
        print(more_recent_game.code)
    return Button(
        "Next Game" if more_recent_game else "Make Game",
        id="next_game",
        cls="btn btn-success",
        hx_post=app.url_path_for("continue_game") if not more_recent_game else None,
        hx_get=app.url_path_for("play_game", game_code=more_recent_game.code)
        if more_recent_game
        else None,
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


def Selections(game: Game, is_update: bool = True, visible_tokens: "set[str] | None" = None):
    selection_containers = []
    for card in game.cards:
        if card.is_guessed:
            continue
        if visible_tokens is not None:
            relevant_selections = [s for s in card.selections if s.token in visible_tokens]
        else:
            relevant_selections = card.selections
        selection_count = len(relevant_selections)
        if selection_count == 0:
            continue
        if selection_count > MAX_SELECTION_COUNT:
            selection_pill_text = f"{SELECTION_TEXT} X {selection_count}"
        else:
            selection_pill_text = SELECTION_TEXT * selection_count
        row, col = card.to_row_col()
        selection_containers.append(
            Div(
                cls="position-relative",
                style=f"grid-area: {row} / {col} / {row} / {col}; pointer-events: none;",
            )(
                Span(
                    selection_pill_text,
                    cls=f"text-bg-light border position-absolute translate-middle badge rounded-pill z-3",
                    style="top: 10%; left: 10%;",
                )
            )
        )
    return Div(
        id="selections", style="display: contents", hx_swap_oob="true" if is_update else None
    )(*selection_containers)


def check_and_set_winner(game: "Game") -> None:
    """Detect win/loss condition and set game.winner. Does NOT commit."""
    if game.winner is not None:
        return

    # Black card revealed → active team loses (or generic game-over in non-warden)
    for card in game.cards:
        if card.kind == GameCardKind.BLACK and card.is_guessed:
            if game.session.has_warden and game.active_team:
                game.winner = "BLUE" if game.active_team == "RED" else "RED"
            else:
                game.winner = "BLACK"
            return

    red_total = sum(1 for c in game.cards if c.kind == GameCardKind.RED)
    red_done = sum(1 for c in game.cards if c.kind == GameCardKind.RED and c.is_guessed)
    if red_total > 0 and red_done == red_total:
        game.winner = "RED"
        return

    blue_total = sum(1 for c in game.cards if c.kind == GameCardKind.BLUE)
    blue_done = sum(1 for c in game.cards if c.kind == GameCardKind.BLUE and c.is_guessed)
    if blue_total > 0 and blue_done == blue_total:
        game.winner = "BLUE"
        return


def WinModal(game: "Game", viewer_token: "str | None" = None, is_update: bool = True):
    container = Div(id="win-modal-container", hx_swap_oob="true" if is_update else None)
    if game.winner is None:
        return container

    winner = game.winner

    # Determine viewer-specific headline
    if game.session.has_warden and viewer_token:
        pr = get_player_session_role(game.session_id, viewer_token)
        if pr and pr.role == "WARDEN":
            headline = f"{'🔴' if winner == 'RED' else '🔵'} {winner.title()} Team Wins!" if winner != "BLACK" else "☠️ Black Card Revealed!"
            won = winner != "BLACK"
        elif pr and pr.team == winner:
            headline = "🎉 You Win!"
            won = True
        elif winner == "BLACK":
            headline = "☠️ Black Card Revealed — Game Over"
            won = False
        else:
            headline = "You Lose"
            won = False
    elif winner == "BLACK":
        headline = "☠️ Black Card Revealed — Game Over"
        won = False
    else:
        headline = f"{'🔴' if winner == 'RED' else '🔵'} {winner.title()} Team Wins!"
        won = True

    header_cls = (
        "bg-danger text-white" if winner == "RED"
        else "bg-primary text-white" if winner == "BLUE"
        else "bg-dark text-white"
    )

    if won and winner in ("RED", "BLUE"):
        colors = '["#dc3545","#ff6b6b","#fff"]' if winner == "RED" else '["#0d6efd","#6ea8fe","#fff"]'
        confetti_code = (
            f"var end=Date.now()+4000;"
            f"(function f(){{"
            f"confetti({{particleCount:3,angle:60,spread:55,origin:{{x:0}},colors:{colors}}});"
            f"confetti({{particleCount:3,angle:120,spread:55,origin:{{x:1}},colors:{colors}}});"
            f"if(Date.now()<end)requestAnimationFrame(f);}})();"
        )
    else:
        confetti_code = ""

    return container(
        Div(cls="modal fade", id="winModal", tabindex="-1", aria_hidden="true")(
            Div(cls="modal-dialog modal-dialog-centered")(
                Div(cls="modal-content")(
                    Div(cls=f"modal-header {header_cls} border-0")(
                        H2(headline, cls="modal-title w-100 text-center fw-bold fs-2 py-3"),
                    )
                )
            )
        ),
        Script(
            f"bootstrap.Modal.getOrCreateInstance(document.getElementById('winModal')).show();"
            f"{confetti_code}"
        ),
    )


def TeamBackground(active_team: "str | None", is_update: bool = True):
    """Style element that tints the page background to the active team's color."""
    if active_team == "RED":
        bg = "background-color: rgba(220, 53, 70, 0.12) !important;"
    elif active_team == "BLUE":
        bg = "background-color: rgba(13, 110, 253, 0.12) !important;"
    else:
        bg = ""
    return Style(
        f"body {{ {bg} }}",
        id="team-bg-style",
        hx_swap_oob="true" if is_update else None,
    )


def TurnIndicator(active_team: "str | None", winner: "str | None" = None, is_update: bool = True):
    if active_team is None and winner is None:
        return None
    if winner in ("RED", "BLUE"):
        team_name = "Red Team" if winner == "RED" else "Blue Team"
        badge_cls = "bg-danger" if winner == "RED" else "bg-primary"
        label = "Winner: "
    elif winner == "BLACK":
        team_name = "Black Card"
        badge_cls = "bg-dark"
        label = "Game Over: "
    else:
        team_name = "Red Team" if active_team == "RED" else "Blue Team"
        badge_cls = "bg-danger" if active_team == "RED" else "bg-primary"
        label = "Current Turn: "
    return Div(
        Span(label, cls="fw-bold"),
        Span(team_name, cls=f"badge {badge_cls}"),
        id="turn-indicator",
        hx_swap_oob="true" if is_update else None,
        cls="mb-2",
    )


def EndTurnButton(game_code: str, is_update: bool = True):
    return Button(
        "End Turn",
        cls="btn btn-warning me-2",
        id="end-turn-btn",
        hx_post=app.url_path_for("end_turn", game_code=game_code),
        hx_swap="none",
        hx_swap_oob="true" if is_update else None,
    )


def AutoConfirmButton(game_code: str, is_update: bool = True):
    return Button(
        "Auto-Confirm Most Selected",
        cls="btn btn-danger me-2",
        id="auto-confirm-btn",
        hx_post=app.url_path_for("auto_confirm_card", game_code=game_code),
        hx_swap="none",
        hx_swap_oob="true" if is_update else None,
    )


def WardenLinksPanel(join_codes: list, game_code: str):
    """Panel with copyable join links for each team/role combination."""
    labels = {
        ("RED", "SPYMASTER"): ("Red Spymaster", "btn-outline-danger"),
        ("RED", "VIEWER"): ("Red Viewer", "btn-outline-danger"),
        ("BLUE", "SPYMASTER"): ("Blue Spymaster", "btn-outline-primary"),
        ("BLUE", "VIEWER"): ("Blue Viewer", "btn-outline-primary"),
    }
    rows = []
    for jc in join_codes:
        label, btn_cls = labels.get((jc.team, jc.role), (f"{jc.team} {jc.role}", "btn-outline-secondary"))
        url = f"{SITE_URL}/join/{jc.code}"
        qr_url = app.url_path_for("qr_code", join_code=jc.code)
        copy_link_js = (
            f"navigator.clipboard.writeText('{url}')"
            f".catch(function(){{ prompt('Copy this link:', '{url}'); }});"
        )
        copy_qr_js = (
            f"fetch('{qr_url}')"
            f".then(function(r){{return r.blob();}})"
            f".then(function(b){{return navigator.clipboard.write([new ClipboardItem({{'image/png':b}})]);}});"
        )
        rows.append(
            Div(cls="d-flex align-items-center mb-2")(
                Button(
                    f"Copy {label} Link",
                    cls=f"btn {btn_cls} me-2",
                    type="button",
                    onclick=copy_link_js,
                ),
                Button(
                    "📋 QR",
                    cls="btn btn-outline-secondary btn-sm",
                    type="button",
                    title=f"Copy {label} QR code",
                    onclick=copy_qr_js,
                ),
            )
        )
    return Div(cls="mb-3 p-3 border rounded")(
        P(Strong("Join Links (share with players):"), cls="mb-2"),
        *rows,
    )


@app.get("/qr/{join_code:str}")
def qr_code(request: Request):
    import qrcode
    import io
    from starlette.responses import Response

    join_code = request.path_params["join_code"]
    join_code_record = session.scalar(
        select(SessionJoinCode).filter(SessionJoinCode.code == join_code)
    )
    if join_code_record is None:
        return Response(status_code=404)

    url = f"{SITE_URL}/join/{join_code}"
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


@app.get("/play")
def play(request: Request):
    tags = session.scalars(select(Tag)).all()
    return Page(
        request,
        "Play",
        Form(
            cls="pt-5",
            hx_post=app.url_path_for("make_game"),
            hx_swap="none",
            hx_vals='js:{"tags": Array.from(qsa(".card-tag:checked")).map(check => check.value), "warden_mode": qs("#warden_mode").checked ? "on" : ""}',
        )(
            # surely there is a better way... for some reason I get parse errors
            #   on empty messages
            # seems to be an open issue: https://github.com/Kludex/python-multipart/issues/38
            Input(name="dummy_value", value="1", hidden=True),
            Div(cls="d-flex align-items-center flex-wrap gap-2")(
                Button(
                    "Make Game",
                    type="submit",
                    cls="btn btn-primary",
                ),
                *[
                    (
                        Input(
                            name=f"tag-{tag.rowid}",
                            value=tag.rowid,
                            cls="card-tag form-check-input",
                            type="checkbox",
                        ),
                        Label(tag.name),
                    )
                    for tag in tags
                ],
                Input(
                    id="warden_mode",
                    name="warden_mode",
                    cls="form-check-input",
                    type="checkbox",
                ),
                Label("Warden Mode", cls="form-check-label"),
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


@dataclass
class MakeGameData:
    tags: list[str] = field(default_factory=list)
    warden_mode: str = ""  # "on" when checkbox checked


@app.post("/play")
def make_game(request: Request, game_data: MakeGameData):
    if len(game_data.tags) == 0:
        return Message(Div(f"Please select some categories for the game"), kind=MessageKind.ERROR)

    token = request.session.get(SITE_TOKEN)
    is_warden = bool(game_data.warden_mode)

    # case to make a new session
    game_session = Session(
        has_warden=is_warden,
        warden_token=token if is_warden else None,
    )
    session.add(game_session)
    # need to generate that id
    session.flush()
    groupers = [
        SessionTagGrouper(session_id=game_session.id, tag_id=tag_id) for tag_id in game_data.tags
    ]
    session.add_all(groupers)
    session.flush()

    try:
        game = game_session.create_game()
    except NotEnoughCards as err:
        session.rollback()
        # this was the first session that was being made which means
        #   the tags cards was not enough to fill a single game
        return Message(
            Div(
                f"You need {err.needed_cards} cards to play a game but those tags only add up to {err.cards_left} cards."
            ),
            kind=MessageKind.ERROR,
        )

    if is_warden:
        # Register warden's role
        session.add(PlayerSessionRole(
            token=token,
            session_id=game_session.id,
            team="WARDEN",
            role="WARDEN",
        ))
        # Generate 4 join codes: red/blue × spymaster/viewer
        join_codes = [
            SessionJoinCode(
                code=secrets.token_urlsafe(JOIN_CODE_BYTES),
                session_id=game_session.id,
                team=team,
                role=role,
            )
            for team, role in [
                ("RED", "SPYMASTER"),
                ("RED", "VIEWER"),
                ("BLUE", "SPYMASTER"),
                ("BLUE", "VIEWER"),
            ]
        ]
        session.add_all(join_codes)

    session.commit()
    return HttpHeader("HX-Redirect", app.url_path_for("play_game", game_code=game.code))


def _resolve_join_code(join_code: str) -> "tuple[SessionJoinCode, Game] | None":
    """Look up a join code and return (SessionJoinCode, most_recent_game) or None."""
    jc = session.scalar(select(SessionJoinCode).filter(SessionJoinCode.code == join_code))
    if jc is None:
        return None
    most_recent = session.scalar(
        select(Game)
        .filter(Game.session_id == jc.session_id)
        .order_by(desc(Game.rowid))
        .limit(1)
    )
    if most_recent is None:
        return None
    return jc, most_recent


def _upsert_player_role(token: str, jc: "SessionJoinCode") -> None:
    """Always sync the current session token to the join code's team/role."""
    stmt = sqlite_insert(PlayerSessionRole).values(
        token=token,
        session_id=jc.session_id,
        team=jc.team,
        role=jc.role,
    ).on_conflict_do_update(set_={"team": jc.team, "role": jc.role})
    session.execute(stmt)
    session.commit()


@app.get("/join/{join_code:str}")
def join_game(request: Request):
    join_code = request.path_params["join_code"]
    result = _resolve_join_code(join_code)
    if result is None:
        return RedirectResponse(app.url_path_for("play"))
    jc, most_recent = result
    _upsert_player_role(request.session.get(SITE_TOKEN), jc)
    url = f"{app.url_path_for('play_game', game_code=most_recent.code)}?join={join_code}"
    return RedirectResponse(url)


@app.post(f"{PARTIALS_PREFIX}/join_game")
def join_game_form(request: Request, join_code: str):
    """Handle join code form submission from the 'join required' page."""
    result = _resolve_join_code(join_code)
    if result is None:
        return Message(Div("Invalid join code"), kind=MessageKind.ERROR)
    jc, most_recent = result
    _upsert_player_role(request.session.get(SITE_TOKEN), jc)
    url = f"{app.url_path_for('play_game', game_code=most_recent.code)}?join={join_code}"
    return HttpHeader("HX-Redirect", url)


# this is the same route for make_game and continue only difference
#    is the wording of the button for the user
@app.post("/continue_game")
async def continue_game(request: Request, game_code: str, session_id: int):
    # Current idea is to require one of the past game codes be sent with
    #    this request
    # Since I do not want to make people log in this seems like a relatively
    #    secure option because as now people may just hit this url with a any session_id
    # If this is not the most recent game of the session then instead of making it would
    #    make sense to just redirect to the newest game
    game = session.scalar(
        select(Game)
        .options(joinedload(Game.session))
        .filter(Game.session_id == session_id)
        .filter(Game.code == game_code)
    )
    if game is None:
        return Message(Div("The game session no longer exists"), kind=MessageKind.ERROR)

    game_session = game.session

    # In warden mode, only the warden can make new games
    if game_session.has_warden:
        token = request.session.get(SITE_TOKEN)
        if token != game_session.warden_token:
            return Message(Div("Only the warden can start a new game"), kind=MessageKind.ERROR)

    most_recent_game_code = session.scalar(
        select(Game.code).filter(Game.session_id == session_id).order_by(desc(Game.rowid)).limit(1)
    )
    assert most_recent_game_code is not None

    if most_recent_game_code != game.code:
        return HttpHeader(
            "HX-Redirect", app.url_path_for("play_game", game_code=most_recent_game_code)
        )
    # update this to make sure to push the updated new game button
    game.last_updated = datetime.now()

    try:
        game = game_session.create_game()
    except NotEnoughCards as err:
        session.rollback()
        return Message(
            Div(
                f"There's only {err.cards_left} cards left to play within this session and you need {err.needed_cards} to play a game!"
            ),
            kind=MessageKind.ERROR,
        )

    if game_session.has_warden:
        # Demote all spymasters to viewers for the new game
        for pr in session.scalars(
            select(PlayerSessionRole)
            .filter(PlayerSessionRole.session_id == game_session.id)
            .filter(PlayerSessionRole.role == "SPYMASTER")
        ).all():
            pr.role = "VIEWER"

        # Rotate spymaster join codes so new spymasters can be chosen
        for old_code in session.scalars(
            select(SessionJoinCode)
            .filter(SessionJoinCode.session_id == game_session.id)
            .filter(SessionJoinCode.role == "SPYMASTER")
        ).all():
            session.delete(old_code)
        session.flush()
        for team in ("RED", "BLUE"):
            session.add(SessionJoinCode(
                code=secrets.token_urlsafe(JOIN_CODE_BYTES),
                session_id=game_session.id,
                team=team,
                role="SPYMASTER",
            ))

    session.commit()
    await broadcast_redirect(game_code, game.code)
    return HttpHeader("HX-Redirect", app.url_path_for("play_game", game_code=game.code))


# done as a separate route to play_game for error handling and later possible spymaster locking
@app.post(f"{PARTIALS_PREFIX}/find_game")
def find_game(game_code: str):
    game = session.scalar(select(Game).filter(Game.code == game_code.upper()))
    if game is None:
        return Message(Div(f"The game `{game_code}` could not be found"), kind=MessageKind.ERROR)

    return HttpHeader("HX-Redirect", app.url_path_for("play_game", game_code=game.code))


def _score_row(game: "Game"):
    red_guessed = len([c for c in game.cards if c.kind == GameCardKind.RED and c.is_guessed])
    red = len([c for c in game.cards if c.kind == GameCardKind.RED])
    blue_guessed = len([c for c in game.cards if c.kind == GameCardKind.BLUE and c.is_guessed])
    blue = len([c for c in game.cards if c.kind == GameCardKind.BLUE])
    black_guessed = len([c for c in game.cards if c.kind == GameCardKind.BLACK and c.is_guessed])
    black = len([c for c in game.cards if c.kind == GameCardKind.BLACK])
    tan_guessed = len([c for c in game.cards if c.kind == GameCardKind.TAN and c.is_guessed])
    tan = len([c for c in game.cards if c.kind == GameCardKind.TAN])
    return Div(
        Span(cls="pe-3")("Red:", Span(id=repr(GameCardKind.RED))(f"{red_guessed}/{red}")),
        Span(cls="pe-3")("Blue:", Span(id=repr(GameCardKind.BLUE))(f"{blue_guessed}/{blue}")),
        Span(cls="pe-3")("Black:", Span(id=repr(GameCardKind.BLACK))(f"{black_guessed}/{black}")),
        Span(cls="pe-3")("Tan:", Span(id=repr(GameCardKind.TAN))(f"{tan_guessed}/{tan}")),
    )


@app.get("/play/{game_code:str}")
def play_game(request: Request, role: str | None = None, join: str | None = None):
    game_code = request.path_params["game_code"]
    token = request.session.get(SITE_TOKEN)
    game = session.scalar(
        select(Game)
        .filter(Game.code == game_code)
        .options(joinedload(Game.cards).joinedload(GameCard.selections))
    )
    if game is None:
        return HttpHeader("HX-Redirect", app.url_path_for("play"))

    game_session = game.session  # lazy load

    # Redirect to newest game in this session if the player missed a "next game" event
    most_recent_code = session.scalar(
        select(Game.code)
        .filter(Game.session_id == game.session_id)
        .order_by(desc(Game.rowid))
        .limit(1)
    )
    if most_recent_code and most_recent_code != game_code:
        dest = app.url_path_for("play_game", game_code=most_recent_code)
        if join:
            dest = f"{dest}?join={join}"
        return HttpHeader("HX-Redirect", dest)

    # --- Warden mode flow ---
    if game_session.has_warden:
        # Upsert role from join code on every load so it always tracks the current cookie
        if join:
            jc = session.scalar(select(SessionJoinCode).filter(SessionJoinCode.code == join))
            if jc and jc.session_id == game_session.id:
                _upsert_player_role(token, jc)

        # Is this person the warden?
        if token == game_session.warden_token:
            join_codes = session.scalars(
                select(SessionJoinCode).filter(SessionJoinCode.session_id == game_session.id)
            ).all()
            visible_tokens = get_visible_tokens(game, token)
            display_team = game.winner if game.winner in ("RED", "BLUE") else game.active_team
            return Page(
                request,
                "Play (Warden)",
                Style(board_css),
                TeamBackground(display_team, is_update=False),
                UserSelectedStyle(None, is_update=False),
                Div(hx_ext="ws", ws_connect=app.url_path_for("play_connect", game_code=game_code)),
                Div(id="game-redirect"),
                TurnIndicator(game.active_team, game.winner, is_update=False),
                GameBoard(game, is_update=False, visible_tokens=visible_tokens),
                _score_row(game),
                NextGameButton(game, is_update=False),
                Div(cls="mt-2")(
                    EndTurnButton(game_code, is_update=False),
                    AutoConfirmButton(game_code, is_update=False),
                    ConfirmButton(game.code, is_update=False),
                ),
                WardenLinksPanel(join_codes, game_code),
                WinModal(game, token, is_update=False),
                MessageStack(),
            )

        # Is this player registered?
        player_role = get_player_session_role(game_session.id, token)
        if player_role is None:
            # Show join code entry
            return Page(
                request,
                "Play (Join Required)",
                Div(cls="mt-5")(
                    H3("Join Code Required"),
                    P("This game requires a join code. Use the link your warden shared, or enter the code below."),
                    Form(
                        hx_post=app.url_path_for("join_game_form"),
                        hx_swap="none",
                    )(
                        Input(name="dummy_value", value="1", hidden=True),
                        Div(cls="d-flex gap-2 mt-3")(
                            Button("Join", cls="btn btn-primary", type="submit"),
                            Div(cls="input-group w-auto")(
                                Input(
                                    cls="form-control",
                                    name="join_code",
                                    placeholder="Join Code",
                                ),
                                Span("Join Code", cls="input-group-text"),
                            ),
                        ),
                    ),
                ),
                MessageStack(),
            )

        # Render board for registered warden-game player
        is_spymaster = player_role.role == "SPYMASTER"
        team_label = f"{player_role.team.title()} {'Spymaster' if is_spymaster else 'Viewer'}"
        visible_tokens = get_visible_tokens(game, token)
        display_team = game.winner if game.winner in ("RED", "BLUE") else game.active_team
        return Page(
            request,
            f"Play ({team_label})",
            Style(board_css),
            Style(
                "\n".join(
                    f".unselected-card-{card.index} {{ {card.kind.to_styles()}; }}"
                    for card in game.cards
                )
            ) if is_spymaster else None,
            TeamBackground(display_team, is_update=False),
            UserSelectedStyle(None, is_update=False),
            Div(hx_ext="ws", ws_connect=f"{app.url_path_for('play_connect', game_code=game_code)}?join={join}"),
            Div(id="game-redirect"),
            Div(cls="mb-1")(
                Span("Your team: ", cls="fw-bold"),
                Span(
                    f"{player_role.team.title()} {'Spymaster' if is_spymaster else 'Viewer'}",
                    cls=f"badge {'bg-danger' if player_role.team == 'RED' else 'bg-primary'}",
                ),
            ),
            TurnIndicator(game.active_team, game.winner, is_update=False),
            GameBoard(game, is_update=False, visible_tokens=visible_tokens),
            _score_row(game),
            Div(id="next_game"),
            WinModal(game, token, is_update=False),
            MessageStack(),
        )

    # --- Non-warden game (existing behavior) ---
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

    bg_team = game.winner if game.winner in ("RED", "BLUE") else None
    return Page(
        request,
        "Play",
        Style(board_css),
        Style(
            "\n".join(
                f".unselected-card-{card.index} {{ {card.kind.to_styles()}; }}"
                for card in game.cards
            )
        ) if role == repr(GameRole.SPYMASTER) else None,
        TeamBackground(bg_team, is_update=False) if bg_team else None,
        UserSelectedStyle(None, is_update=False),
        Div(hx_ext="ws", ws_connect=app.url_path_for("play_connect", game_code=game_code)),
        Div(id="game-redirect"),
        H2(f"Game Code: {game_code}"),
        GameBoard(game, is_update=False),
        _score_row(game),
        NextGameButton(game, is_update=False)
        if (role == repr(GameRole.SPYMASTER)) or (role == repr(GameRole.OPERATIVE))
        else NextGameButton(game, enabled=False, is_update=False),
        ConfirmButton(game.code, is_update=False)
        if (role == repr(GameRole.SPYMASTER)) or (role == repr(GameRole.OPERATIVE))
        else None,
        WinModal(game, is_update=False),
        MessageStack(),
    )


# everything is an oob swap to make it easier to maybe do web connections later for
#   updating the game state
# could implement caching on each game
async def updated_game(game_code: str, last_updated: str | None, viewer_token: str | None = None):
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
        return

    red_guessed = len([c for c in game.cards if c.kind == GameCardKind.RED and c.is_guessed])
    red = len([c for c in game.cards if c.kind == GameCardKind.RED])
    blue_guessed = len([c for c in game.cards if c.kind == GameCardKind.BLUE and c.is_guessed])
    blue = len([c for c in game.cards if c.kind == GameCardKind.BLUE])
    black_guessed = len([c for c in game.cards if c.kind == GameCardKind.BLACK and c.is_guessed])
    black = len([c for c in game.cards if c.kind == GameCardKind.BLACK])
    tan_guessed = len([c for c in game.cards if c.kind == GameCardKind.TAN and c.is_guessed])
    tan = len([c for c in game.cards if c.kind == GameCardKind.TAN])

    # Compute per-viewer selection visibility for warden games
    game_session = game.session
    visible_tokens = None
    if game_session.has_warden and viewer_token is not None:
        visible_tokens = get_visible_tokens(game, viewer_token)

    # Winner color overrides active-team background
    display_team = game.winner if game.winner in ("RED", "BLUE") else game.active_team

    return (
        *[CardBoard(c, game) for c in game.cards if c.is_guessed],
        Span(id=repr(GameCardKind.RED), hx_swap_oob="true")(f"{red_guessed}/{red}"),
        Span(id=repr(GameCardKind.BLUE), hx_swap_oob="true")(f"{blue_guessed}/{blue}"),
        Span(id=repr(GameCardKind.BLACK), hx_swap_oob="true")(f"{black_guessed}/{black}"),
        Span(id=repr(GameCardKind.TAN), hx_swap_oob="true")(f"{tan_guessed}/{tan}"),
        NextGameButton(game),
        Selections(game, visible_tokens=visible_tokens),
        TurnIndicator(game.active_team, game.winner) if game_session.has_warden else None,
        TeamBackground(display_team) if (game_session.has_warden or game.winner) else None,
        WinModal(game, viewer_token),
    )


@dataclass
class WebSocketPlayerData:
    websocket: WebSocket
    game_code: str
    last_updated: str | None = None  # isoformatted
    token: str | None = None  # session token for personalized updates
    join_code: str | None = None  # join code carried in URL — used to preserve it in redirects


players: dict[str, WebSocketPlayerData] = {}


async def broadcast_redirect(old_game_code: str, new_game_code: str):
    """Push a JS redirect to all players currently watching old_game_code."""
    base_url = app.url_path_for("play_game", game_code=new_game_code)
    for uid, player in dict(players).items():
        if player.game_code != old_game_code:
            continue
        new_url = f"{base_url}?join={player.join_code}" if player.join_code else base_url
        html = to_xml(
            Div(id="game-redirect", hx_swap_oob="true")(
                Script(f"window.location.href = '{new_url}';")
            )
        )
        try:
            await player.websocket.send_text(html)
        except Exception:
            del players[uid]


async def update_game():
    for uid, player in dict(players).items():
        try:
            game = session.scalar(select(Game).where(Game.code == player.game_code))
            if game is None:
                del players[uid]
                continue
            fhtml_game = await updated_game(player.game_code, player.last_updated, player.token)
            if fhtml_game is None:
                # case where game shouldn't be updated
                print("skipping send")
                continue
            await player.websocket.send_text(to_xml(fhtml_game))
            player.last_updated = str(game.last_updated)
        except Exception as err:
            print(err)
            del players[uid]


class PlayConnect(WebSocketEndpoint):
    encoding = "http"

    async def on_connect(self, websocket: WebSocket):
        await websocket.accept()
        game_code = websocket.path_params["game_code"]
        self.uuid = str(uuid.uuid4())
        token = websocket.session.get(SITE_TOKEN) if hasattr(websocket, "session") else None
        join_code = websocket.query_params.get("join")
        players[self.uuid] = WebSocketPlayerData(
            websocket=websocket, game_code=game_code, token=token, join_code=join_code
        )

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

    # In warden mode, spymasters cannot confirm guesses
    if game.session.has_warden:
        token = request.session.get(SITE_TOKEN)
        player_role = get_player_session_role(game.session_id, token)
        if player_role is not None and player_role.role == "SPYMASTER":
            return Message(Div("Spymasters cannot confirm guesses in warden mode"), kind=MessageKind.WARNING)

    game_card.is_guessed = True
    check_and_set_winner(game)
    game.last_updated = datetime.now()
    session.commit()
    await update_game()
    return UserSelectedStyle(None), ConfirmButton(game_code, None)


@app.post(f"{PARTIALS_PREFIX}/end_turn/{{game_code:str}}")
async def end_turn(request: Request):
    game_code = request.path_params["game_code"]
    token = request.session.get(SITE_TOKEN)
    game = session.scalar(
        select(Game)
        .filter(Game.code == game_code)
        .options(joinedload(Game.session))
    )
    if game is None:
        return Message(Div("Game not found"), kind=MessageKind.ERROR)
    if not game.session.has_warden or token != game.session.warden_token:
        return Message(Div("Only the warden can end turns"), kind=MessageKind.ERROR)

    game.active_team = "BLUE" if game.active_team == "RED" else "RED"
    game.last_updated = datetime.now()
    session.commit()
    await update_game()
    return TurnIndicator(game.active_team, game.winner), TeamBackground(game.active_team)


@app.post(f"{PARTIALS_PREFIX}/auto_confirm/{{game_code:str}}")
async def auto_confirm_card(request: Request):
    game_code = request.path_params["game_code"]
    token = request.session.get(SITE_TOKEN)
    game = session.scalar(
        select(Game)
        .filter(Game.code == game_code)
        .options(
            joinedload(Game.session),
            joinedload(Game.cards).joinedload(GameCard.selections),
        )
    )
    if game is None:
        return Message(Div("Game not found"), kind=MessageKind.ERROR)
    if not game.session.has_warden or token != game.session.warden_token:
        return Message(Div("Only the warden can auto-confirm"), kind=MessageKind.ERROR)

    # Get active team's non-spymaster tokens
    active_tokens = {
        r.token
        for r in session.scalars(
            select(PlayerSessionRole)
            .filter(PlayerSessionRole.session_id == game.session_id)
            .filter(PlayerSessionRole.team == game.active_team)
            .filter(PlayerSessionRole.role != "SPYMASTER")
        ).all()
    }

    # Find unguessed card with most selections from active team
    best_card = None
    best_count = 0
    for card in game.cards:
        if card.is_guessed:
            continue
        count = sum(1 for s in card.selections if s.token in active_tokens)
        if count > best_count:
            best_count = count
            best_card = card

    if best_card is None or best_count == 0:
        return Message(Div("No selections from active team to confirm"), kind=MessageKind.WARNING)

    best_card.is_guessed = True
    check_and_set_winner(game)
    game.last_updated = datetime.now()
    session.commit()
    await update_game()
    return ()


@app.post(f"{PARTIALS_PREFIX}/select_card/{{game_code:str}}")
async def select_card(request: Request, game_card_id: int):
    game_code = request.path_params["game_code"]
    # i think there's a better sqlalchemy api for this query
    game = session.scalar(
        select(Game).filter(Game.code == game_code).options(joinedload(Game.session))
    )
    game_exists = game is not None
    assert game_exists
    token = request.session.get(SITE_TOKEN)
    assert token is not None

    card = GameCard.get(game_card_id)
    assert card is not None
    game.last_updated = datetime.now()
    session.commit()
    current_selection = session.scalar(
        select(Selection).filter(Selection.token == token).filter(Selection.game_code == game_code)
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
