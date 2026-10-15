"""Development goals: add from a gap, edit, change status. Shown on the person profile."""

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from competence.core import services
from competence.core.models import GOAL_STATUSES
from competence.web.app import ConnDep
from competence.web.routes.pages import is_htmx, render, see_other
from competence.web.schemas import GoalCreateForm, GoalEditForm, GoalStatusForm

router = APIRouter(include_in_schema=False)

STATUS_LABELS = {"open": "Open", "in_progress": "In progress", "done": "Done", "dropped": "Dropped"}


def profile_url(round_id: int, person_id: int) -> str:
    return f"/round/{round_id}/person/{person_id}/profile#goals"


def _assessment(conn, round_id: int, person_id: int):
    a = services.get_assessment(conn, round_id, person_id)
    if a is None:
        raise HTTPException(404, "no assessment for this person in this round")
    return a


def _form(request: Request, conn, a, values: dict, goal=None, error: str | None = None, status_code: int = 200):
    rows = services.rating_rows(conn, a.id, team_only=False)  # a goal can be about any skill
    return render(
        request,
        "goal_form.html",
        {
            "a": a,
            "goal": goal,
            "v": values,
            "error": error,
            "skills": rows,
            "rounds": services.list_rounds(conn)[::-1],
            "statuses": STATUS_LABELS,
        },
        status_code,
    )


@router.get("/round/{round_id}/person/{person_id}/goals/new")
def new_goal(
    request: Request, conn: ConnDep, round_id: int, person_id: int, skill_id: Annotated[int | None, Query()] = None
) -> HTMLResponse:
    """Blank goal, or pre-filled from a gap: the skill and the target for the person's role this round."""
    a = _assessment(conn, round_id, person_id)
    row = next((r for r in services.rating_rows(conn, a.id, team_only=False) if r.skill_id == skill_id), None)
    values = {"title": "", "skill_id": None, "target_level": None, "due_round_id": None, "actions": "", "notes": ""}
    if row is not None:
        values.update(
            skill_id=row.skill_id,
            target_level=row.target,
            title=f"Reach level {row.target} in {row.name}" if row.target is not None else f"Develop {row.name}",
        )
    return _form(request, conn, a, values)


@router.post("/round/{round_id}/person/{person_id}/goals")
def create_goal(
    request: Request, conn: ConnDep, round_id: int, person_id: int, form: Annotated[GoalCreateForm, Form()]
):
    a = _assessment(conn, round_id, person_id)
    try:
        services.create_goal(
            conn,
            person_id,
            round_id,
            form.title,
            form.skill_id,
            form.target_level,
            form.due_round_id,
            form.actions,
            form.notes,
        )
    except (services.Invalid, services.NotFound) as e:
        return _form(request, conn, a, form.model_dump(), error=str(e), status_code=400)
    return see_other(profile_url(round_id, person_id))


def _goal_or_404(conn, goal_id: int):
    try:
        return services.get_goal(conn, goal_id)
    except services.NotFound as e:
        raise HTTPException(404, str(e)) from e


@router.get("/goal/{goal_id}/edit")
def edit_goal(request: Request, conn: ConnDep, goal_id: int, round: Annotated[int, Query()]) -> HTMLResponse:
    g = _goal_or_404(conn, goal_id)
    a = _assessment(conn, round, g.person_id)
    values = {
        "title": g.title,
        "skill_id": g.skill_id,
        "target_level": g.target_level,
        "due_round_id": g.due_round.id if g.due_round else None,
        "status": g.status,
        "actions": g.actions,
        "notes": g.notes,
    }
    return _form(request, conn, a, values, goal=g)


@router.post("/goal/{goal_id}")
def update_goal(request: Request, conn: ConnDep, goal_id: int, form: Annotated[GoalEditForm, Form()]):
    g = _goal_or_404(conn, goal_id)
    a = _assessment(conn, form.round_id, g.person_id)
    try:
        services.update_goal(
            conn, goal_id, form.title, form.target_level, form.due_round_id, form.status, form.actions, form.notes
        )
    except (services.Invalid, services.NotFound) as e:
        return _form(
            request, conn, a, {**form.model_dump(), "skill_id": g.skill_id}, goal=g, error=str(e), status_code=400
        )
    return see_other(profile_url(form.round_id, g.person_id))


@router.post("/goal/{goal_id}/status")
def goal_status(request: Request, conn: ConnDep, goal_id: int, form: Annotated[GoalStatusForm, Form()]):
    g = _goal_or_404(conn, goal_id)
    a = _assessment(conn, form.round_id, g.person_id)

    def shown():  # a profile only lists goals created in or before its round
        return next((p for p in services.list_goals(conn, g.person_id, form.round_id) if p.goal.id == goal_id), None)

    if is_htmx(request) and shown() is None:
        raise HTTPException(404, "this goal isn't shown in that round")
    services.set_goal_status(conn, goal_id, form.status)
    if is_htmx(request):
        progress = shown()
        return render(
            request, "fragments/goal_row.html", {"gp": progress, "a": a, "statuses": STATUS_LABELS, "saved": True}
        )
    return see_other(profile_url(form.round_id, g.person_id))


assert set(STATUS_LABELS) == set(GOAL_STATUSES)
