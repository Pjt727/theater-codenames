from fasthtml.common import *
from fasthtml.svg import Path

_hdrs = (
    # boostrap cdn v5.3
    Link(
        href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css",
        rel="stylesheet",
        integrity="sha384-QWTKZyjpPEjISv5WaRU9OFeRpok6YctnYmDr5pNlyT2bRjXh0JMhjY6hW+ALEwIH",
        crossorigin="anonymous",
    ),
    Script(
        src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js",
        integrity="sha384-YvpcrYf0tY3lHB60NNkmXc5s9fDVZLESaAA55NDzOxhy9GkcIdslK1eN7N6jIeHz",
        crossorigin="anonymous",
    ),
    # some javascript abbreviations
    #  (i like this better than using some of the other mini js frameworks)
    Script("""
               var qs = document.querySelector.bind(document);
               var qsa = document.querySelectorAll.bind(document);
               var qsa = document.querySelectorAll.bind(document);
               var onload = (callback) => document.addEventListener('DOMContentLoaded', callback)
               """),
)

app, rt = fast_app(hdrs=_hdrs, pico=False, live=True)

PARTIALS_PREFIX = "/partials"
ASSETS_PATH = "./assets"
SCRIPTS_PATH = "./scripts"
CSS_PATH = "./css"
