from fasthtml.common import *


def Page(req: Request, title: str, *c):
    return (
        Title(title),
        Hr(),
        Body(Container(*c)),
    )
