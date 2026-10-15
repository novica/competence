"""Compare two rounds, for the team or for one person, with a trend per skill across all rounds."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from competence.core import services
from competence.web.app import ConnDep
from competence.web.routes.pages import _clean_tag, clean_team, render
from competence.web.schemas import OptionalId

router = APIRouter(include_in_schema=False)


@router.get("/compare")
def compare_page(
    request: Request,
    conn: ConnDep,
    from_: Annotated[OptionalId, Query(alias="from")] = None,
    to: Annotated[OptionalId, Query()] = None,
    person: Annotated[OptionalId, Query()] = None,
    tag: Annotated[str | None, Query()] = None,
    team: Annotated[OptionalId, Query()] = None,
    changed: Annotated[bool, Query()] = False,
) -> HTMLResponse:
    """Defaults: the newest round against the one before it."""
    rounds = services.list_rounds(conn)  # newest first
    tag, tags = _clean_tag(conn, tag)
    team, teams = clean_team(conn, team)
    people = services.list_people(conn)
    ctx = {
        "rounds": rounds,
        "people": people,
        "tag": tag,
        "tags": tags,
        "team": team,
        "teams": teams,
        "person_id": person,
        "changed": changed,
        "cmp": None,
        "pcmp": None,
        "error": None,
    }
    if len(rounds) < 2:
        ctx["error"] = "Comparing needs at least two rounds."
        return render(request, "compare.html", ctx)
    to = to if to is not None else rounds[0].id
    if from_ is None:
        prev = services.previous_round(conn, to)
        from_ = prev.id if prev else next(r.id for r in rounds if r.id != to)
    ctx.update(from_id=from_, to_id=to)
    try:
        if person is None:
            ctx["cmp"] = services.compare_team(conn, from_, to, tag, team)
        else:
            ctx["pcmp"] = services.compare_person(conn, from_, to, person, tag)
    except services.Invalid as e:
        ctx["error"] = str(e)
        return render(request, "compare.html", ctx, 400)
    except services.NotFound as e:
        raise HTTPException(404, str(e)) from e
    return render(request, "compare.html", ctx)
