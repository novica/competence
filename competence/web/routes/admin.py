"""Admin: skills, roles, the targets grid, level texts, people and rounds. Every change is a POST that redirects back."""

from datetime import date
from itertools import groupby
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response

from competence.core import admin, certifications, services
from competence.core import catalogue as cat
from competence.core.backup import backup_database
from competence.web.app import ConnDep
from competence.web.routes.pages import _clean_tag, is_htmx, render, see_other
from competence.web.schemas import (
    ActiveForm,
    BackForm,
    CategoryRenameForm,
    CategoryTargetForm,
    LevelLabelsForm,
    MoveForm,
    NameForm,
    PersonForm,
    RoleCreateForm,
    RoundEditForm,
    SkillCreateForm,
    SkillEditForm,
    TargetForm,
    TeamCategoriesForm,
)

router = APIRouter(prefix="/admin", include_in_schema=False)


def back_to(back: str, default: str) -> str:
    """Only ever redirect within the admin pages (no open redirect via the hidden field)."""
    return back if back.startswith("/admin") and "//" not in back and "\\" not in back else default


def _404(e: services.NotFound):
    return HTTPException(404, str(e))


@router.get("")
def index(request: Request, conn: ConnDep) -> HTMLResponse:
    skills = admin.list_skills(conn)
    roles = admin.list_roles(conn)
    return render(
        request,
        "admin/index.html",
        {
            "n_skills": sum(s.retired_on is None for s in skills),
            "n_retired": sum(s.retired_on is not None for s in skills),
            "n_roles": sum(r.retired_on is None for r in roles),
            "n_people": len(admin.list_all_people(conn)),
            "n_rounds": len(admin.list_all_rounds(conn)),
            "n_categories": len(admin.categories(conn)),
            "n_certs": len(certifications.list_certifications(conn, include_retired=False)),
            "n_teams": len(services.list_teams(conn, include_retired=False)),
        },
    )


# ---------- level texts ----------


def _levels_page(request, conn, labels=None, error=None, status_code=200, saved=False) -> HTMLResponse:
    return render(
        request,
        "admin/levels.html",
        {"labels": labels or services.level_labels(conn), "error": error, "saved": saved},
        status_code,
    )


@router.get("/levels")
def levels_page(request: Request, conn: ConnDep, saved: Annotated[bool, Query()] = False) -> HTMLResponse:
    return _levels_page(request, conn, saved=saved)


@router.post("/levels")
def save_levels(request: Request, conn: ConnDep, form: Annotated[LevelLabelsForm, Form()]):
    try:
        admin.set_level_labels(conn, form.labels())
    except services.Invalid as e:
        return _levels_page(request, conn, form.labels(), str(e), 400)
    return see_other("/admin/levels?saved=true")


# ---------- skills ----------


@router.get("/skills")
def skills_page(
    request: Request,
    conn: ConnDep,
    cat: Annotated[str | None, Query()] = None,
    tag: Annotated[str | None, Query()] = None,
    show: Annotated[str, Query()] = "all",
) -> HTMLResponse:
    return _skills_page(request, conn, cat, tag, show)


def _skills_page(request, conn, cat=None, tag=None, show="all", error=None, status_code=200) -> HTMLResponse:
    cats = admin.categories(conn)
    cat = cat if cat in cats else None
    tag, tags = _clean_tag(conn, tag, include_retired=True)
    skills = admin.list_skills(conn, cat, tag, include_retired=show != "active")
    groups = [(c, list(items)) for c, items in groupby(skills, key=lambda s: s.category)]
    query = urlencode({k: v for k, v in {"cat": cat, "tag": tag, "show": show if show != "all" else None}.items() if v})
    return render(
        request,
        "admin/skills.html",
        {
            "groups": groups,
            "categories": cats,
            "cat": cat,
            "tag": tag,
            "tags": tags,
            "show": show,
            "back": "/admin/skills" + ("?" + query if query else ""),
            "count": len(skills),
            "error": error,
        },
        status_code,
    )


def _skill_form(request, conn, values: dict, skill=None, error=None, status_code=200):
    roles = [r for r in admin.list_roles(conn) if r.retired_on is None]
    return render(
        request,
        "admin/skill_form.html",
        {
            "skill": skill,
            "v": values,
            "error": error,
            "roles": roles,
            "categories": admin.categories(conn),
            "tags": services.tags(conn, include_retired=True),
            "all_skills": admin.list_skills(conn, include_retired=False),
        },
        status_code,
    )


@router.get("/skills/new")
def new_skill(request: Request, conn: ConnDep, category: Annotated[str | None, Query()] = None) -> HTMLResponse:
    """Targets start at the median of the category's other skills; pick the category and press Suggest to refresh."""
    category = category or (admin.categories(conn) or [""])[0]
    values = {
        "category": category,
        "name": "",
        "description": "",
        "tag": "",
        "priority": 2,
        "experts_needed": 1,
        "copy_ratings_from": None,
        "targets": admin.suggested_targets(conn, category),
    }
    return _skill_form(request, conn, values)


@router.post("/skills")
def create_skill(request: Request, conn: ConnDep, form: Annotated[SkillCreateForm, Form()]):
    values = {**form.model_dump(), "targets": dict(zip(form.target_role, form.target_level))}
    if len(form.target_role) != len(form.target_level):
        return _skill_form(request, conn, values, error="targets don't line up with roles", status_code=400)
    try:
        sid, _copied = admin.add_skill(
            conn,
            form.category,
            form.name,
            form.description,
            form.tag,
            form.priority,
            form.experts_needed,
            values["targets"],
            form.copy_ratings_from,
        )
    except (services.Invalid, services.NotFound) as e:
        return _skill_form(request, conn, values, error=str(e), status_code=400)
    return see_other(f"/admin/skills?{urlencode({'cat': form.category})}#skill-{sid}")


def _skill_or_404(conn, skill_id):
    try:
        return admin.get_skill(conn, skill_id)
    except services.NotFound as e:
        raise _404(e) from e


@router.get("/skills/{skill_id}/edit")
def edit_skill(request: Request, conn: ConnDep, skill_id: int) -> HTMLResponse:
    s = _skill_or_404(conn, skill_id)
    values = {
        "category": s.category,
        "name": s.name,
        "description": s.description,
        "tag": s.tag,
        "priority": s.priority,
        "experts_needed": s.experts_needed,
    }
    return _skill_form(request, conn, values, skill=s)


@router.post("/skills/{skill_id}")
def update_skill(request: Request, conn: ConnDep, skill_id: int, form: Annotated[SkillEditForm, Form()]):
    s = _skill_or_404(conn, skill_id)
    try:
        admin.update_skill(
            conn, skill_id, form.category, form.name, form.description, form.tag, form.priority, form.experts_needed
        )
    except services.Invalid as e:
        return _skill_form(request, conn, form.model_dump(), skill=s, error=str(e), status_code=400)
    return see_other(f"/admin/skills?{urlencode({'cat': form.category})}#skill-{skill_id}")


@router.post("/skills/{skill_id}/move")
def move_skill(conn: ConnDep, skill_id: int, form: Annotated[MoveForm, Form()]):
    _skill_or_404(conn, skill_id)
    admin.move_skill(conn, skill_id, form.direction)
    return see_other(back_to(form.back, "/admin/skills") + f"#skill-{skill_id}")


@router.post("/skills/{skill_id}/retire")
def retire_skill(conn: ConnDep, skill_id: int, form: Annotated[BackForm, Form()]):
    _skill_or_404(conn, skill_id)
    admin.retire_skill(conn, skill_id)
    return see_other(back_to(form.back, "/admin/skills") + f"#skill-{skill_id}")


@router.post("/skills/{skill_id}/unretire")
def unretire_skill(conn: ConnDep, skill_id: int, form: Annotated[BackForm, Form()]):
    _skill_or_404(conn, skill_id)
    admin.unretire_skill(conn, skill_id)
    return see_other(back_to(form.back, "/admin/skills") + f"#skill-{skill_id}")


@router.post("/skills/{skill_id}/delete")
def delete_skill(request: Request, conn: ConnDep, skill_id: int, form: Annotated[BackForm, Form()]):
    _skill_or_404(conn, skill_id)
    try:
        admin.delete_skill(conn, skill_id)
    except services.Invalid as e:
        return _skills_page(request, conn, error=str(e), status_code=400)
    return see_other(back_to(form.back, "/admin/skills"))


@router.post("/categories/rename")
def rename_category(request: Request, conn: ConnDep, form: Annotated[CategoryRenameForm, Form()]):
    try:
        admin.rename_category(conn, form.old, form.new)
    except services.NotFound as e:
        raise _404(e) from e
    except services.Invalid as e:
        return _skills_page(request, conn, cat=form.old, error=str(e), status_code=400)
    return see_other(f"/admin/skills?{urlencode({'cat': form.new})}")


@router.get("/catalogue.toml")
def export_catalogue(conn: ConnDep) -> Response:
    """The catalogue in use (no people or ratings), to seed another database with `competence init --catalogue`."""
    today = date.today().isoformat()
    header = (
        f"Competence catalogue, exported on {today}: roles, skills, targets, certifications and level texts\n"
        "in use (no people or ratings). Start a new database from it with:\n\n"
        "    competence init --catalogue this-file.toml\n\n"
        "The layout is explained in empty.toml, which ships with the app."
    )
    return Response(
        cat.dumps(admin.catalogue_of(conn), header),
        media_type="application/toml; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="competence-catalogue-{today}.toml"'},
    )


# ---------- roles ----------


def _roles_page(request, conn, error=None, status_code=200):
    return render(request, "admin/roles.html", {"roles": admin.list_roles(conn), "error": error}, status_code)


@router.get("/roles")
def roles_page(request: Request, conn: ConnDep) -> HTMLResponse:
    return _roles_page(request, conn)


@router.post("/roles")
def create_role(request: Request, conn: ConnDep, form: Annotated[RoleCreateForm, Form()]):
    try:
        admin.add_role(conn, form.name, form.copy_targets_from)
    except (services.Invalid, services.NotFound) as e:
        return _roles_page(request, conn, str(e), 400)
    return see_other("/admin/roles")


@router.post("/roles/{role_id}")
def rename_role(request: Request, conn: ConnDep, role_id: int, form: Annotated[NameForm, Form()]):
    try:
        admin.rename_role(conn, role_id, form.name)
    except services.NotFound as e:
        raise _404(e) from e
    except services.Invalid as e:
        return _roles_page(request, conn, str(e), 400)
    return see_other("/admin/roles")


@router.post("/roles/{role_id}/retire")
def retire_role(request: Request, conn: ConnDep, role_id: int):
    try:
        admin.retire_role(conn, role_id)
    except services.NotFound as e:
        raise _404(e) from e
    except services.Invalid as e:
        return _roles_page(request, conn, str(e), 400)
    return see_other("/admin/roles")


@router.post("/roles/{role_id}/unretire")
def unretire_role(conn: ConnDep, role_id: int):
    try:
        admin.unretire_role(conn, role_id)
    except services.NotFound as e:
        raise _404(e) from e
    return see_other("/admin/roles")


# ---------- teams ----------


def _teams_page(request, conn, error=None, status_code=200):
    teams = services.list_teams(conn)
    return render(
        request,
        "admin/teams.html",
        {
            "teams": teams,
            "counts": admin.team_counts(conn),
            "n_categories": len(admin.categories(conn)),
            "chosen": {t.id: len(services.team_categories(conn, t.id)) for t in teams},
            "error": error,
        },
        status_code,
    )


@router.get("/teams")
def teams_page(request: Request, conn: ConnDep) -> HTMLResponse:
    return _teams_page(request, conn)


@router.post("/teams")
def create_team(request: Request, conn: ConnDep, form: Annotated[NameForm, Form()]):
    try:
        admin.add_team(conn, form.name)
    except services.Invalid as e:
        return _teams_page(request, conn, str(e), 400)
    return see_other("/admin/teams")


@router.post("/teams/{team_id}")
def rename_team(request: Request, conn: ConnDep, team_id: int, form: Annotated[NameForm, Form()]):
    try:
        admin.rename_team(conn, team_id, form.name)
    except services.NotFound as e:
        raise _404(e) from e
    except services.Invalid as e:
        return _teams_page(request, conn, str(e), 400)
    return see_other("/admin/teams")


def _team_or_404(conn, team_id: int):
    try:
        return services.get_team(conn, team_id)
    except services.NotFound as e:
        raise _404(e) from e


def _team_categories_page(request, conn, team, chosen: list[str], error=None, status_code=200):
    return render(
        request,
        "admin/team_categories.html",
        {"team": team, "categories": admin.categories(conn), "chosen": set(chosen), "error": error},
        status_code,
    )


@router.get("/teams/{team_id}/categories")
def team_categories_page(request: Request, conn: ConnDep, team_id: int) -> HTMLResponse:
    team = _team_or_404(conn, team_id)
    return _team_categories_page(request, conn, team, services.team_categories(conn, team_id))


@router.post("/teams/{team_id}/categories")
def save_team_categories(request: Request, conn: ConnDep, team_id: int, form: Annotated[TeamCategoriesForm, Form()]):
    team = _team_or_404(conn, team_id)
    try:
        admin.set_team_categories(conn, team_id, form.category)
    except services.Invalid as e:
        return _team_categories_page(request, conn, team, form.category, str(e), 400)
    return see_other("/admin/teams")


@router.post("/teams/{team_id}/retire")
def retire_team(conn: ConnDep, team_id: int):
    try:
        admin.set_team_retired(conn, team_id, True)
    except services.NotFound as e:
        raise _404(e) from e
    return see_other("/admin/teams")


@router.post("/teams/{team_id}/unretire")
def unretire_team(conn: ConnDep, team_id: int):
    try:
        admin.set_team_retired(conn, team_id, False)
    except services.NotFound as e:
        raise _404(e) from e
    return see_other("/admin/teams")


# ---------- targets grid ----------


@router.get("/targets")
def targets_page(
    request: Request,
    conn: ConnDep,
    cat: Annotated[str | None, Query()] = None,
    tag: Annotated[str | None, Query()] = None,
) -> HTMLResponse:
    """Active roles x active skills. Defaults to the first category; 'all' shows every skill."""
    cats = admin.categories(conn)
    if cat != "all":
        cat = cat if cat in cats else (cats[0] if cats else None)
    tag, tags = _clean_tag(conn, tag)
    skills = admin.list_skills(conn, None if cat == "all" else cat, tag, include_retired=False)
    roles = [r for r in admin.list_roles(conn) if r.retired_on is None]
    query = urlencode({k: v for k, v in {"cat": cat, "tag": tag}.items() if v})
    return render(
        request,
        "admin/targets.html",
        {
            "skills": skills,
            "roles": roles,
            "targets": admin.targets_for(conn, [s.id for s in skills]),
            "categories": cats,
            "cat": cat,
            "tag": tag,
            "tags": tags,
            "back": "/admin/targets?" + query,
        },
    )


@router.post("/targets/{role_id}/{skill_id}")
def set_target(request: Request, conn: ConnDep, role_id: int, skill_id: int, form: Annotated[TargetForm, Form()]):
    try:
        admin.set_target(conn, role_id, skill_id, form.level)
    except services.NotFound as e:
        raise _404(e) from e
    if is_htmx(request):
        return render(
            request,
            "admin/fragments/target_cell.html",
            {"role_id": role_id, "skill_id": skill_id, "level": form.level, "back": form.back, "saved": True},
        )
    return see_other(back_to(form.back, "/admin/targets") + f"#skill-{skill_id}")


@router.post("/targets/{role_id}")
def set_category_targets(request: Request, conn: ConnDep, role_id: int, form: Annotated[CategoryTargetForm, Form()]):
    """Every shown skill of a category at once, for one role. A full page reload shows the new grid.

    It overwrites the role's exceptions in that category, so the database is backed up first: the way back
    is that backup (in data/backups/, like the ones taken at start-up).
    """
    backup_database(request.app.state.db_path, request.app.state.backups)
    try:
        admin.set_category_targets(conn, role_id, form.category, form.level, form.tag or None)
    except services.NotFound as e:
        raise _404(e) from e
    return see_other(back_to(form.back, "/admin/targets"))


# ---------- people and rounds ----------


def _people_page(request, conn, error=None, status_code=200):
    return render(request, "admin/people.html", {"people": admin.list_all_people(conn), "error": error}, status_code)


@router.get("/people")
def people_page(request: Request, conn: ConnDep) -> HTMLResponse:
    return _people_page(request, conn)


@router.post("/people")
def create_person(request: Request, conn: ConnDep, form: Annotated[PersonForm, Form()]):
    try:
        services.create_person(conn, form.name)
    except services.Invalid as e:
        return _people_page(request, conn, str(e), 400)
    return see_other("/admin/people")


@router.post("/people/{person_id}")
def rename_person(request: Request, conn: ConnDep, person_id: int, form: Annotated[NameForm, Form()]):
    try:
        admin.rename_person(conn, person_id, form.name)
    except services.NotFound as e:
        raise _404(e) from e
    except services.Invalid as e:
        return _people_page(request, conn, str(e), 400)
    return see_other("/admin/people")


@router.post("/people/{person_id}/active")
def person_active(conn: ConnDep, person_id: int, form: Annotated[ActiveForm, Form()]):
    try:
        admin.set_person_active(conn, person_id, form.active)
    except services.NotFound as e:
        raise _404(e) from e
    return see_other("/admin/people")


def _rounds_page(request, conn, error=None, status_code=200):
    return render(request, "admin/rounds.html", {"rounds": admin.list_all_rounds(conn), "error": error}, status_code)


@router.get("/rounds")
def rounds_page(request: Request, conn: ConnDep) -> HTMLResponse:
    return _rounds_page(request, conn)


@router.post("/rounds/{round_id}")
def update_round(request: Request, conn: ConnDep, round_id: int, form: Annotated[RoundEditForm, Form()]):
    try:
        admin.update_round(conn, round_id, form.label, form.held_on.isoformat() if form.held_on else None, form.notes)
    except services.NotFound as e:
        raise _404(e) from e
    except services.Invalid as e:
        return _rounds_page(request, conn, str(e), 400)
    return see_other("/admin/rounds")


@router.post("/rounds/{round_id}/delete")
def delete_round(request: Request, conn: ConnDep, round_id: int):
    try:
        admin.delete_round(conn, round_id)
    except services.NotFound as e:
        raise _404(e) from e
    except services.Invalid as e:
        return _rounds_page(request, conn, str(e), 400)
    return see_other("/admin/rounds")
