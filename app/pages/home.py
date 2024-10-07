from fasthtml.common import *
from sqlalchemy import func
from models.game import *
from starlette.requests import Request
from make_app import app
from pages.components import Page


@app.get("/")
def home(request: Request):
    return Page(
        request,
        "Home",
        A("Play", href=app.url_path_for("play")),
        Br(),
        A("Game Wiki", href="https://en.wikipedia.org/wiki/Codenames_(board_game)"),
    )
