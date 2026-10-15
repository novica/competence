"""FastAPI application factory and the two request checks that keep it local."""

import os
import sqlite3
import zlib
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from starlette.middleware.trustedhost import TrustedHostMiddleware

from competence.core.catalogue import DESIRES, LEVELS
from competence.core.db import connect

WEB = Path(__file__).resolve().parent
HTMX = "htmx-2.0.11.min.js"
LOCAL_HOSTS = ("127.0.0.1", "localhost")
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def is_same_origin(request: Request) -> bool:
    """True if a state-changing request came from this app's own pages.

    Browsers send Origin on form posts and fetches; Referer is the fallback. A request with neither
    is refused, so another website open in the browser can't post forms to localhost.
    """
    own = f"{request.url.scheme}://{request.headers.get('host', '')}"
    origin = request.headers.get("origin")
    if origin is not None:
        return origin == own
    referer = request.headers.get("referer")
    return referer is not None and (referer == own or referer.startswith(own + "/"))


def format_number(value, decimals: int = 1) -> str:
    """Blank as an en dash; rounded, then whole numbers without decimals (1.04 -> 1, not 1.0)."""
    if value is None:
        return "–"
    if isinstance(value, int):
        return str(value)
    value = round(value, decimals)
    if value.is_integer():
        return str(int(value))
    return f"{value:.{decimals}f}"


def format_signed(value, decimals: int = 1) -> str:
    """A change, with an explicit sign: +2, -0.5, ±0 (also for changes that round to zero)."""
    if value is None:
        return ""
    if round(value, decimals) == 0:
        return "±0"
    return ("+" if value > 0 else "−") + format_number(abs(value), decimals)


def tag_badge(tag: str) -> Markup:
    """A small label with a colour worked out from the tag's name, so each tag keeps its colour. Blank = nothing."""
    if not tag:
        return Markup("")
    return Markup('<span class="tag" style="--hue: {}">{}</span>').format(zlib.crc32(tag.encode()) % 360, tag)


def create_app(
    db_path: Path, *, demo: bool = False, backups: Path | None = None, allowed_hosts: tuple[str, ...] = LOCAL_HOSTS
) -> FastAPI:
    # No JSON API yet, so there is nothing to document at /docs.
    app = FastAPI(title="Competence", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db_path = db_path
    app.state.backups = backups or db_path.parent / "backups"  # also used before risky bulk changes
    templates = Jinja2Templates(directory=WEB / "templates")
    templates.env.globals.update(demo=demo, levels=LEVELS, desires=DESIRES, htmx=HTMX)
    templates.env.filters.update(num=format_number, signed=format_signed, tag_badge=tag_badge)
    app.state.templates = templates

    @app.middleware("http")
    async def refuse_cross_origin_writes(request: Request, call_next):
        if request.method in UNSAFE_METHODS and not is_same_origin(request):
            return PlainTextResponse("Cross-origin request refused.", status_code=403)
        return await call_next(request)

    # Added last = runs first. Rejects any Host other than localhost, which blocks DNS-rebinding
    # attacks where a website re-points its own name at 127.0.0.1 to read this app's pages.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))

    app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")

    from competence.web.routes import admin, certifications, compare, goals, pages

    app.include_router(pages.router)
    app.include_router(goals.router)
    app.include_router(compare.router)
    app.include_router(admin.router)
    app.include_router(certifications.router)
    return app


ENV_DB, ENV_DEMO, ENV_BACKUPS = "MATRIX_DB", "MATRIX_DEMO", "MATRIX_BACKUPS"


def create_app_from_env() -> FastAPI:
    """For `app.py --reload`: uvicorn re-imports the app in a fresh process, so the settings travel as env vars."""
    return create_app(
        Path(os.environ[ENV_DB]), demo=os.environ.get(ENV_DEMO) == "1", backups=Path(os.environ[ENV_BACKUPS])
    )


def get_conn(request: Request) -> Iterator:
    conn = connect(request.app.state.db_path, check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


ConnDep = Annotated[sqlite3.Connection, Depends(get_conn)]
