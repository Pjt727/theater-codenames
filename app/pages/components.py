from fasthtml.common import *
from make_app import ASSETS_PATH, app


def Settings(req: Request):
    return Nav(cls="navbar bg-body-tertiary")(
        Div(cls="container-fluid")(
            A("Home", cls="navbar-brand", href=app.url_path_for("home")), Div()
        )
    )


def Page(req: Request, title: str, *c):
    # implementing simple, limited SPA where everything but the containers switch
    # this is not really that useful in this case but you could imagine something like
    #   this could be nice
    if "hx-request" in req.headers.keys():
        return (
            Container(*c, id="rootEl"),
            HttpHeader("HX-Push-Url", str(req.url)),
            HttpHeader("HX-Retarget", "#rootEl"),
            HttpHeader("HX-Reswap", "outerHTML"),
        )
    return (
        Title(title),
        Hr(),
        Body(Container(*c, id="rootEl")),
    )


class MessageKind(enum.Enum):
    INFO = 0
    SUCCESS = 1
    WARNING = 2
    ERROR = 3

    def to_path(self) -> str:
        if self == MessageKind.INFO:
            # TODO CHANGE TO MAYBE A DIFFERENT ICON
            return f"{ASSETS_PATH}/success.svg"
        if self == MessageKind.SUCCESS:
            return f"{ASSETS_PATH}/success.svg"
        elif self == MessageKind.WARNING:
            return f"{ASSETS_PATH}/warning.svg"
        elif self == MessageKind.ERROR:
            return f"{ASSETS_PATH}/error.svg"
        raise ValueError("Invalid message kind")


def Message(*c, title="Codenames", title_secondary: str = "", kind: MessageKind = MessageKind.INFO):
    return Div(hx_swap_oob="beforeend:#messages")(
        Div(
            role="alert",
            aria_live="assertive",
            aria_atomic="true",
            data_bs_autohide="false"
            if kind == MessageKind.ERROR or kind == MessageKind.WARNING
            else None,
            cls="toast",
        )(
            Script("bootstrap.Toast.getOrCreateInstance(me()).show()"),
            Div(cls="toast-header")(
                Img(cls="me-2", src=kind.to_path(), width="25px"),
                Strong(title, cls="me-auto"),
                Small(title_secondary, cls="text-body-secondary"),
                Button(type="button", data_bs_dismiss="toast", aria_label="Close", cls="btn-close"),
            ),
            Div(*c, cls="toast-body"),
        ),
    )


# might want to find a differetn place for the messages to live if you want them
#   to persist on partial page refreshes (the hx-push stuff)
MessageStack = lambda: Div(id="messages", cls="toast-container p-4 position-absolute top-0 end-0")
