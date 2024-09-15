from fasthtml.common import *
from models.game import *
from make_app import app, PARTIALS_PREFIX


@app.get("/play")
def play():
    game = Game.create()
    return game
