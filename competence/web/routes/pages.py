"""HTML pages. Each route reads the request, calls one service and renders a page or an htmx fragment."""

from datetime import date
from itertools import groupby
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from competence.core import certifications, dashboard, metrics, services
from competence.web.app import ConnDep
from competence.web.schemas import RatingForm, RoundForm, StartForm, TeamChangeForm

router = APIRouter(include_in_schema=False)


def render(request: Request, name: str, context: dict, status_code: int = 200) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(request, name, context, status_code=status_code)


def see_other(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request") == "true"


def _round_or_404(conn, round_id: int):
    try:
        return services.get_round(conn, round_id)
    except services.NotFound as e:
        raise HTTPException(404, str(e)) from e


# ---------- home, rounds and people ----------


@router.get("/")
def home(request: Request, conn: ConnDep, team: Annotated[int | None, Query()] = None) -> HTMLResponse:
    team, teams = clean_team(conn, team)
    return render(
        request,
        "home.html",
        {"d": dashboard.dashboard(conn, team), "team": team, "teams": teams, "link": query_link({"team": team})},
    )


def _rounds_page(request: Request, conn, error: str | None = None, status_code: int = 200) -> HTMLResponse:
    return render(request, "rounds.html", {"rounds": dashboard.rounds_progress(conn), "error": error}, status_code)


@router.get("/rounds")
def rounds_page(request: Request, conn: ConnDep) -> HTMLResponse:
    return _rounds_page(request, conn)


@router.post("/rounds")
def create_round(request: Request, conn: ConnDep, form: Annotated[RoundForm, Form()]):
    held_on = form.held_on.isoformat() if form.held_on else None
    try:
        rnd = services.create_round(conn, form.label, held_on)
    except services.Invalid as e:
        return _rounds_page(request, conn, str(e), 400)
    return see_other(f"/round/{rnd.id}")


@router.get("/people")
def people_page(request: Request, conn: ConnDep, team: Annotated[int | None, Query()] = None) -> HTMLResponse:
    team, teams = clean_team(conn, team)
    return render(
        request,
        "people.html",
        {
            "people": dashboard.people(conn, team),
            "certs": bool(certifications.list_certifications(conn, include_retired=False)),
            "team": team,
            "teams": teams,
            "link": query_link({"team": team}),
        },
    )


@router.get("/guide")
def guide(request: Request, conn: ConnDep) -> HTMLResponse:
    return render(
        request,
        "guide.html",
        {
            "labels": services.level_labels(conn),
            "flags": list(metrics.FLAG_RULES.items()),
            "cert_statuses": list(certifications.STATUS_LABELS.items()),
            "cert_rules": certifications.STATUS_RULES,
            "db_path": request.app.state.db_path,
            "backups": request.app.state.backups,
        },
    )


# ---------- a round ----------


@router.get("/round/{round_id}")
def round_page(request: Request, conn: ConnDep, round_id: int) -> HTMLResponse:
    rnd = _round_or_404(conn, round_id)
    return render(
        request,
        "round.html",
        {
            "round": rnd,
            "people": services.round_status(conn, round_id),
            "roles": services.list_active_roles(conn),
            "teams": services.list_teams(conn, include_retired=False),
        },
    )


@router.post("/round/{round_id}/person/{person_id}/start")
def start_assessment(conn: ConnDep, round_id: int, person_id: int, form: Annotated[StartForm, Form()]):
    try:
        services.start_assessment(conn, round_id, person_id, form.role_id, form.team_id)
    except services.NotFound as e:
        raise HTTPException(404, str(e)) from e
    except services.Invalid as e:
        if services.get_assessment(conn, round_id, person_id) is None:
            raise HTTPException(400, str(e)) from e
        # already started (e.g. double click): just carry on to the form
    return see_other(f"/round/{round_id}/person/{person_id}")


@router.post("/round/{round_id}/person/{person_id}/team")
def change_team(conn: ConnDep, round_id: int, person_id: int, form: Annotated[TeamChangeForm, Form()]):
    """The person's team for this round only; other rounds keep theirs."""
    try:
        services.set_assessment_team(conn, round_id, person_id, form.team_id)
    except services.NotFound as e:
        raise HTTPException(404, str(e)) from e
    except services.Invalid as e:
        raise HTTPException(400, str(e)) from e
    return see_other(f"/round/{round_id}#person-{person_id}")


# ---------- rating form ----------


@router.get("/round/{round_id}/person/{person_id}")
def rate_page(
    request: Request,
    conn: ConnDep,
    round_id: int,
    person_id: int,
    tag: Annotated[str | None, Query()] = None,
    all: Annotated[bool, Query()] = False,
) -> HTMLResponse:
    """The skills of the person's team's categories; ?all=true shows every category (e.g. to rate one outside them)."""
    _round_or_404(conn, round_id)
    a = services.get_assessment(conn, round_id, person_id)
    if a is None:
        return see_other(f"/round/{round_id}")
    tag, tags = _clean_tag(conn, tag)
    team_cats = services.team_categories(conn, a.team.id) if a.team else []
    rows = services.rating_rows(conn, a.id, tag, team_only=not all)
    groups = [(cat, list(items)) for cat, items in groupby(rows, key=lambda r: r.category)]
    prev = services.previous_assessment(conn, a)
    return render(
        request,
        "rate.html",
        {
            "a": a,
            "prev": prev,
            "groups": groups,
            "tag": tag,
            "tags": tags,
            "count": len(rows),
            "labels": services.level_labels(conn),
            "team_cats": team_cats,
            "show_all": all,
            "link": query_link({"tag": tag, "all": "true" if all else None}),
        },
    )


@router.post("/round/{round_id}/person/{person_id}/skill/{skill_id}")
def save_rating(
    request: Request, conn: ConnDep, round_id: int, person_id: int, skill_id: int, form: Annotated[RatingForm, Form()]
):
    """One skill's ratings. htmx gets the updated row back; a plain form post goes back to the form."""
    a = services.get_assessment(conn, round_id, person_id)
    if a is None:
        raise HTTPException(404, "no assessment for this person in this round")
    try:
        row = services.save_rating(conn, a.id, skill_id, form.self_level, form.agreed, form.desire)
    except services.NotFound as e:
        raise HTTPException(404, str(e)) from e
    if is_htmx(request):
        return render(
            request,
            "fragments/skill_row.html",
            {"a": a, "r": row, "saved": True, "labels": services.level_labels(conn)},
        )
    return see_other(f"/round/{round_id}/person/{person_id}#skill-{skill_id}")


# ---------- team overview and person profile ----------

# column key -> attribute of SkillOverview / its stats; first click sorts in the listed direction
TEAM_COLUMNS = {
    "skill": ("name", "asc"),
    "priority": ("priority", "desc"),
    "assessed": ("stats.assessed", "desc"),
    "median_level": ("stats.median_level", "desc"),
    "median_desire": ("stats.median_desire", "desc"),
    "n3": ("stats.n3", "desc"),
    "n5": ("stats.n5", "desc"),
    "below": ("stats.below", "desc"),
    "shortfall": ("stats.shortfall", "desc"),
    "weighted": ("stats.weighted_shortfall", "desc"),
    "dev": ("stats.dev_candidates", "desc"),
    "competency": ("stats.median_competency", "desc"),
    "flag": ("stats.flag", "asc"),
}


def _attr(obj, dotted: str):
    for part in dotted.split("."):
        obj = getattr(obj, part)
    return obj.lower() if isinstance(obj, str) else obj


def sort_rows(rows: list, column: str | None, direction: str) -> list:
    """Sort by a column, keeping blanks last in either direction. No column = catalogue order."""
    if column not in TEAM_COLUMNS:
        return rows
    path = TEAM_COLUMNS[column][0]
    present = [r for r in rows if _attr(r, path) is not None]
    blank = [r for r in rows if _attr(r, path) is None]
    return sorted(present, key=lambda r: _attr(r, path), reverse=direction == "desc") + blank


def clean_team(conn, team: int | None):
    """The team filter if it names a team (anything else means everyone), and the teams to offer.

    Retired teams stay in the list: their history can still be looked at.
    """
    teams = services.list_teams(conn)
    return (team if team in {t.id for t in teams} else None), teams


def query_link(params: dict):
    """A link builder for filter links: link(team=3) keeps the other parameters, drops blank ones."""

    def link(**changes) -> str:
        q = {k: v for k, v in {**params, **changes}.items() if v}
        return "?" + urlencode(q) if q else "?"

    return link


def _clean_tag(conn, tag: str | None, include_retired: bool = False) -> tuple[str | None, list[str]]:
    """The tag filter if it names a tag in use (anything else means all skills), and the tags to offer."""
    tags = services.tags(conn, include_retired)
    return (tag if tag in tags else None), tags


@router.get("/round/{round_id}/team")
def team_page(
    request: Request,
    conn: ConnDep,
    round_id: int,
    tag: Annotated[str | None, Query()] = None,
    cat: Annotated[str | None, Query()] = None,
    sort: Annotated[str | None, Query()] = None,
    dir: Annotated[str | None, Query()] = None,
    team: Annotated[int | None, Query()] = None,
) -> HTMLResponse:
    _round_or_404(conn, round_id)
    tag, tags = _clean_tag(conn, tag)
    team, teams = clean_team(conn, team)
    ov = services.team_overview(conn, round_id, tag, team)
    categories = [c.category for c in ov.categories]
    cat = cat if cat in categories else None
    direction = dir if dir in ("asc", "desc") else (TEAM_COLUMNS[sort][1] if sort in TEAM_COLUMNS else "asc")
    rows = [r for r in ov.skills if cat is None or r.category == cat]
    rows = sort_rows(rows, sort, direction)
    params = {"team": team, "tag": tag, "cat": cat, "sort": sort if sort in TEAM_COLUMNS else None, "dir": direction}

    def link(**changes) -> str:
        q = {k: v for k, v in {**params, **changes}.items() if v}
        if not q.get("sort"):
            q.pop("dir", None)
        return "?" + urlencode(q) if q else "?"

    def sort_link(column: str) -> str:
        first = TEAM_COLUMNS[column][1]
        flipped = {"asc": "desc", "desc": "asc"}[direction] if params["sort"] == column else first
        return link(sort=column, dir=flipped)

    return render(
        request,
        "team.html",
        {
            "ov": ov,
            "rows": rows,
            "categories": categories,
            "tag": tag,
            "tags": tags,
            "team": team,
            "teams": teams,
            "cat": cat,
            "sort": params["sort"],
            "dir": direction,
            "link": link,
            "sort_link": sort_link,
        },
    )


@router.get("/round/{round_id}/person/{person_id}/profile")
def profile_page(
    request: Request, conn: ConnDep, round_id: int, person_id: int, tag: Annotated[str | None, Query()] = None
) -> HTMLResponse:
    _round_or_404(conn, round_id)
    tag, tags = _clean_tag(conn, tag)
    try:
        profile = services.person_profile(conn, round_id, person_id, tag)
    except services.NotFound:
        return see_other(f"/round/{round_id}")
    from competence.web.routes.goals import STATUS_LABELS

    goals = services.list_goals(conn, person_id, round_id)
    certs = [
        r for r in certifications.person_certifications(conn, person_id).rows if r.status and not r.cert.retired_on
    ]
    return render(
        request,
        "profile.html",
        {
            "p": profile,
            "a": profile.assessment,
            "tag": tag,
            "tags": tags,
            "goals": goals,
            "statuses": STATUS_LABELS,
            "certs": certs,
            "cert_labels": certifications.STATUS_LABELS,
            "today": date.today().isoformat(),
        },
    )


# ---------- heatmap ----------

# metric -> (label, how to read a HeatCell)
HEAT_METRICS = {
    "level": ("Level used", lambda c: c.used),
    "desire": ("Desire", lambda c: c.desire),
    "competency": ("Competency (level × desire)", lambda c: c.competency),
    "gap": ("Gap vs role target", lambda c: c.gap),
}
_FIB_BIN = {0: 0, 1: 1, 2: 1, 3: 2, 5: 3, 8: 4}
_DESIRE_BIN = {1: 0, 2: 2, 3: 4}


def heat_class(metric: str, value: int | None) -> str:
    """CSS class for a cell: five shades for level and competency (0-24), three for desire (1-3), and
    far below/below/at/above for gaps. Blank cells get none."""
    if value is None:
        return ""
    if metric == "gap":
        return "g-far" if value <= -3 else "g-below" if value < 0 else "g-ok" if value == 0 else "g-above"
    if metric == "competency":
        return f"h{0 if value == 0 else 1 if value <= 3 else 2 if value <= 8 else 3 if value <= 15 else 4}"
    if metric == "desire":
        return f"h{_DESIRE_BIN[value]}"
    return f"h{_FIB_BIN[value]}"


@router.get("/round/{round_id}/heatmap")
def heatmap_page(
    request: Request,
    conn: ConnDep,
    round_id: int,
    metric: Annotated[str, Query()] = "level",
    tag: Annotated[str | None, Query()] = None,
    team: Annotated[int | None, Query()] = None,
) -> HTMLResponse:
    _round_or_404(conn, round_id)
    metric = metric if metric in HEAT_METRICS else "level"
    tag, tags = _clean_tag(conn, tag)
    team, teams = clean_team(conn, team)
    hm = services.heatmap(conn, round_id, tag, team)
    groups = [(cat, list(items)) for cat, items in groupby(hm.rows, key=lambda r: r.category)]

    def link(**changes) -> str:
        return "?" + urlencode({k: v for k, v in {"metric": metric, "team": team, "tag": tag, **changes}.items() if v})

    return render(
        request,
        "heatmap.html",
        {
            "hm": hm,
            "groups": groups,
            "metric": metric,
            "tag": tag,
            "tags": tags,
            "team": team,
            "teams": teams,
            "metrics": {k: v[0] for k, v in HEAT_METRICS.items()},
            "read": HEAT_METRICS[metric][1],
            "heat_class": heat_class,
            "link": link,
        },
    )
